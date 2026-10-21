"""
session_runner.py
-----------------
Deterministic session orchestration. Replaces the prompt-driven session
logic in run_session.ps1 — regime detection, gold tape, hot-name filtering,
quota enforcement, signal dispatch, and Telegram session-summary all live
here in code rather than as instructions Claude reinterprets each run.

Usage (called by run_session.ps1):
    python session_runner.py {session_name}

Where session_name is one of:
    premarket_us, premarket_ca, session1, session2, session3, session4
"""
from __future__ import annotations
import html
import sys
import os
import json
import math

# Force UTF-8 on stdout/stderr so emoji prints (🌅 🍁 🟢 🔴) don't crash
# the script when invoked from Windows Task Scheduler under cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from itertools import zip_longest
from typing import Literal

import pandas as pd

# nest_asyncio MUST be applied before any ib_insync or Telethon imports so
# they share a single event loop and asyncio.run() (Telethon) doesn't kill
# ib_insync. This is the canonical fix for this exact conflict.
try:
    import nest_asyncio
    from ib_insync import util as _ibu
    _ibu.startLoop()        # creates + sets a long-lived event loop
    nest_asyncio.apply()    # allows asyncio.run() inside that loop
except Exception:
    pass

from ibkr_client import IBKRClient
from indicators import add_all, latest_snapshot
from signals import score_symbol, maybe_send, TIMEFRAME_RULES
from telegram_notify import send, send_status, send_no_signal, send_session_summary
import filters
import news_fetcher
import news_rss_fetcher
import enrichment
import premarket_scan
import deep_scan
from market_context import (chart_snapshot, usable_pct, canadian_regime,
                            us_regime, format_market_context)
from browser_research import optional_browser_status, format_browser_status
from research_pipeline import verify_shortlist, evidence_note


TRADING_DIR = Path(r"C:\Trading")
LOGS_DIR    = TRADING_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------
# Session config
# ---------------------------------------------------------------
SESSION_CONFIG = {
    "premarket_us": {"label": "🌅 Pre-Market US (05:45)", "do_signals": False, "tf": None},
    "premarket_ca": {"label": "🍁 Pre-Market CA (07:00)", "do_signals": False, "tf": None},
    "session1":     {"label": "Session 1 (08:30)",        "do_signals": True,  "tf": "1-DAY"},
    "session2":     {"label": "Session 2 (11:00)",        "do_signals": True,  "tf": "1-DAY"},
    "session3":     {"label": "Session 3 (12:30)",        "do_signals": True,  "tf": "1-DAY"},
    "session4":     {"label": "Session 4 (13:55)",        "do_signals": False, "tf": None},
}

# v5.5 — quotas raised so news/catalyst-driven names have room alongside the
# momentum names instead of all five competing for 4 slots/day (which is why
# the same trend names kept repeating). Tune freely.
DAILY_SIGNAL_QUOTA    = 8   # max equity BUY signals per day (was 4)
SESSION_SIGNAL_QUOTA  = 3   # max equity BUY signals per session (was 2)
OPTIONS_DAILY_QUOTA   = 4   # max options signals per day (CALL + PUT combined)
OPTIONS_CALL_QUOTA    = 2   # hard daily cap; unused PUT slots are never CALL slots
OPTIONS_PUT_QUOTA     = 2   # hard daily cap; only verified candidates may fill it
HISTORY_DURATION     = "2 Y"

# v5.5 — diversity controls. The 4/day + score-ranking funnel concentrated on a
# handful of persistent uptrend names (CNR fired 13x in a month). These break
# that pattern:
MAX_PER_SECTOR_PER_DAY = 2   # at most N signals from one sector ETF per day
SIGNAL_COOLDOWN_DAYS   = 3   # don't re-fire a name sent in the last N days …
#                              … UNLESS it has a fresh news/catalyst (see below)
CATALYST_POOL_MAX      = 5   # max news-driven names injected into the research
#                              pool per session (cost guard on DeepSeek tokens)


# ---------------------------------------------------------------
# Watchlist + sector groups
# ---------------------------------------------------------------
def load_watchlist() -> list[str]:
    path = TRADING_DIR / "watchlist.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


GOLD_NAMES = {"AEM", "ABX", "BTO", "LUN", "IVN", "AII", "RIC", "CG"}
# v5.5 — kept in sync with the XIT block of filters.SECTOR_MAP. Names here are
# dropped in a 🔴 BEARISH regime (filter_hot_names). The newer high-beta tech
# adds (SHOP/LSPD/TOI/BB/DSG/DCBO/WELL/NDA) were previously missing, so they
# sailed through risk-off tapes untouched.
TECH_NAMES = {"CSU", "KXS", "LMN", "ENGH", "MDA", "CLS",
              "SHOP", "OTEX", "LSPD", "BB", "TOI", "DSG", "DCBO", "WELL", "NDA"}


# ---------------------------------------------------------------
# Yahoo Finance % change helpers (no API key needed)
# ---------------------------------------------------------------
def _yahoo_market_snapshot(symbol: str, now: datetime | None = None) -> dict:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1d&range=5d")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        result = data.get("chart", {}).get("result")
        return chart_snapshot(symbol, result[0] if result else {}, now)
    except Exception as e:
        print(f"[market] {symbol} unavailable: {type(e).__name__}")
        return chart_snapshot(symbol, {}, now)


def _yahoo_pct_change(symbol: str) -> float | None:
    return usable_pct(_yahoo_market_snapshot(symbol))


def _yahoo_price(symbol: str) -> float | None:
    """Just the latest price, used for VIX level."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1d&range=2d")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        return float(result[0].get("meta", {}).get("regularMarketPrice"))
    except Exception:
        return None


def _yahoo_history(symbol: str, currency: str = "CAD",
                   period_days: int = 90) -> pd.DataFrame:
    """
    Yahoo OHLCV fallback when IBKR Gateway is down. Returns a DataFrame
    with lowercase columns (open, high, low, close, volume) indexed by
    bar date. Empty DataFrame on failure.

    v5.4.2: Uses yf_symbols.yf_ticker_candidates() for correct resolution
    of Class-A/B shares (RCI.A → RCI-A.TO) and TSXV names (RCK → RCK.V).
    Tries primary candidate first, falls back through alternatives.
    ETFs and quotes that already carry a suffix (XIC.TO, ^VIX) bypass
    the resolver.
    """
    # Tickers that already carry a suffix or are non-equity quotes
    if (symbol.upper().endswith((".TO", ".V", ".CN"))
            or symbol.startswith("^") or currency.upper() != "CAD"):
        candidates = [symbol]
    else:
        from yf_symbols import yf_ticker_candidates
        candidates = yf_ticker_candidates(symbol, currency=currency)

    range_str = ("5y" if period_days > 504 else "2y" if period_days > 252
                 else "1y" if period_days > 126 else "6mo")
    for sym_yahoo in candidates:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_yahoo}"
               f"?interval=1d&range={range_str}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            result = (data.get("chart", {}).get("result") or [])
            if not result:
                continue
            ts_list = result[0].get("timestamp") or []
            q       = result[0].get("indicators", {}).get("quote", [{}])[0]
            opens   = q.get("open")   or []
            highs   = q.get("high")   or []
            lows    = q.get("low")    or []
            closes  = q.get("close")  or []
            vols    = q.get("volume") or []
            rows = []
            for i, ts in enumerate(ts_list):
                if i >= len(closes) or closes[i] is None:
                    continue
                rows.append({
                    "date":   datetime.fromtimestamp(ts, tz=timezone.utc).date(),
                    "open":   float(opens[i] or closes[i]),
                    "high":   float(highs[i] or closes[i]),
                    "low":    float(lows[i]  or closes[i]),
                    "close":  float(closes[i]),
                    "volume": float(vols[i]  or 0.0),
                })
            if not rows:
                continue
            df = pd.DataFrame(rows)
            df.index = pd.to_datetime(df["date"])
            from zoneinfo import ZoneInfo
            market_now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Toronto"))
            regular_end = ((result[0].get("meta", {}).get("currentTradingPeriod") or {})
                           .get("regular", {}).get("end"))
            before_close = (datetime.now(timezone.utc).timestamp() < regular_end
                            if isinstance(regular_end, (int, float)) else market_now.hour < 16)
            if before_close:
                df = df.loc[df.index.date < market_now.date()]
            df = df.tail(period_days)
            df.attrs["source"] = "Yahoo completed daily bars"
            df.attrs["observed_at"] = datetime.now(timezone.utc).isoformat()
            return df
        except Exception:
            continue
    return pd.DataFrame()


def fetch_bars(client: "IBKRClient | None", symbol: str, duration: str,
               bar_size: str, currency: str = "CAD") -> pd.DataFrame:
    """
    Unified bar fetch: tries IBKR first if connected, falls back to Yahoo.
    Returns a DataFrame with lowercase columns or empty on failure.
    """
    if client is not None:
        try:
            bars = client.get_historical_bars(symbol, duration, bar_size, currency)
            df = pd.DataFrame(bars)
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception:
            pass
    # Yahoo fallback (daily only — weekly/intraday not supported here)
    if "day" in (bar_size or "").lower() or duration.endswith("D") or duration.endswith("Y"):
        import re
        match = re.fullmatch(r"\s*(\d+)\s*([DWMY])\s*", duration.upper())
        period = (int(match[1]) * {"D": 1, "W": 5, "M": 21, "Y": 252}[match[2]]
                  if match else 504)
        return _yahoo_history(symbol, currency=currency, period_days=period)
    return pd.DataFrame()


def determine_regime() -> tuple[str, str, dict]:
    """
    Returns (regime_tag, gold_tag, market_state_dict).

    market_state_dict has keys for downstream use:
      qqq_pct, spy_pct, xic_pct, gold_pct, vix_level, wti_pct,
      sector_etf_pct (dict)
    """
    # Sector ETFs. v5.5: dropped XCM.TO (delisted — was silently returning 0.0
    # and no-opping the telecom gate) and added XUT (utilities), XRE (REITs),
    # ZIN (industrials) so the newer watchlist names actually get a sector gate.
    sector_etfs = ["XEG.TO", "XGD.TO", "XFN.TO", "XIT.TO", "XMA.TO",
                   "XUT.TO", "XRE.TO", "ZIN.TO"]
    now = datetime.now(timezone.utc)
    symbols = ["QQQ", "SPY", "XIC.TO", "GC=F", "CL=F", "NQ=F", "^VIX", *sector_etfs]
    snapshots = {symbol: _yahoo_market_snapshot(symbol, now) for symbol in symbols}
    qqq, spy, xic, gold, wti, nq_f = (
        usable_pct(snapshots[s]) for s in symbols[:6])
    vix = snapshots["^VIX"]
    vix_level = vix.get("price") if vix.get("status") in {"live", "closed"} else None
    sector_pct = {etf.replace(".TO", ""): usable_pct(snapshots[etf]) for etf in sector_etfs}
    regime = canadian_regime(snapshots["XIC.TO"])

    # Gold tag
    if gold is None:
        gold_tag = "GOLD-UNKNOWN (stale or unavailable)"
    elif gold > 0.005:
        gold_tag = "GOLD-STRONG"
    elif gold < -0.005:
        gold_tag = "GOLD-WEAK"
    else:
        gold_tag = "GOLD-FLAT"

    return (regime, gold_tag, {
        "regime": regime, "us_regime": us_regime(snapshots["QQQ"], snapshots["SPY"]),
        "gold_tag": gold_tag, "captured_at": now.isoformat(), "snapshots": snapshots,
        "qqq_pct": qqq, "spy_pct": spy, "xic_pct": xic, "gold_pct": gold,
        "wti_pct": wti, "vix_level": vix_level, "sector_etf_pct": sector_pct,
        "nq_f_pct": nq_f,
    })


# ---------------------------------------------------------------
# Hot-name filter (regime + gold + sector + price action)
# ---------------------------------------------------------------
def filter_hot_names(watchlist: list[str], regime: str, gold_tag: str,
                     sector_pct: dict) -> list[str]:
    hot = []

    is_bearish = "BEARISH" in regime
    gold_strong = "STRONG" in gold_tag

    for sym in watchlist:
        # Drop tech in bearish regime
        if is_bearish and sym in TECH_NAMES:
            continue

        # Always include gold names when gold strong (regime override)
        if gold_strong and sym in GOLD_NAMES:
            hot.append(sym)
            continue

        # Skip gold names when gold weak
        if "WEAK" in gold_tag and sym in GOLD_NAMES:
            continue

        hot.append(sym)

    return hot


# ---------------------------------------------------------------
# Quota tracking
# ---------------------------------------------------------------
def signals_sent_today() -> list[dict]:
    today = datetime.now().strftime("%Y%m%d")
    path = LOGS_DIR / f"signals_sent_{today}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def append_signal_log(signal_dict: dict, session_name: str):
    today = datetime.now().strftime("%Y%m%d")
    path = LOGS_DIR / f"signals_sent_{today}.jsonl"
    record = {
        **signal_dict,
        "session": session_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def options_signals_sent_today() -> list[dict]:
    """Read today's options signal log (separate from equity log)."""
    today = datetime.now().strftime("%Y%m%d")
    path = LOGS_DIR / f"options_signals_sent_{today}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def append_options_signal_log(sig, session_name: str):
    """Append one OptionsSignal to today's options log."""
    today = datetime.now().strftime("%Y%m%d")
    path = LOGS_DIR / f"options_signals_sent_{today}.jsonl"
    record = {
        "symbol":       sig.symbol,
        "option_type":  sig.option_type,
        "strike":       sig.strike,
        "expiry":       sig.expiry,
        "dte":          sig.dte,
        "spot":         sig.spot,
        "spot_reference": sig.spot_reference,
        "est_premium":  sig.est_premium,
        "options_src":  sig.options_src,
        "currency":     sig.currency,
        "chain_source": sig.chain_source,
        "bid":          sig.bid,
        "ask":          sig.ask,
        "quote_time":   sig.quote_time,
        "data_mode":    sig.data_mode,
        "multiplier":   sig.multiplier,
        "confidence":   sig.confidence,
        "session":      session_name,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _rank_signal(sig) -> tuple:
    """Best candidates first after the whole watchlist has been scored."""
    rr = 0.0
    try:
        risk = sig.entry_price - sig.stop_loss
        reward = sig.take_profit - sig.entry_price
        if risk > 0:
            rr = reward / risk
    except Exception:
        rr = 0.0
    return (
        sig.score,
        sig.weighted_score,
        sig.confidence,
        rr,
        -sig.min_score_required,
        sig.symbol,
    )


def _detect_catalyst(symbol: str, sig, df, news_events: list[dict]) -> dict | None:
    """
    v5.5 — catalyst inclusion lane. Scores a name for a *fresh* catalyst,
    independent of whether it passed the technical trend rubric. This is what
    lets news-driven names (WELL/PRL etc.) reach the DeepSeek research pool
    even when they're not in a textbook uptrend.

    Inputs are reused from the scoring loop — no extra network calls:
      sig.indicators carries close / vol_ratio / atr_14 from latest_snapshot
      df is the 60-day OHLCV already fetched for scoring

    Returns {"strength": float, "reasons": [str, ...]} or None.
    """
    reasons: list[str] = []
    strength = 0.0

    # 1) Fresh bullish news (strong ticker/alias match within 24h). news_sentiment
    #    already filters out noisy sector-keyword matches, so banks appearing in
    #    general headlines don't trigger this.
    try:
        delta, headline = filters.news_sentiment(news_events or [], symbol, hours=24)
        if delta > 0 and headline:
            strength += 2.0
            reasons.append(f"Bullish news (24h): {headline[:90]}")
    except Exception:
        pass

    # 2) Price/volume catalyst from the bars we already have — a real move on
    #    real participation (gap/thrust), regardless of trend structure.
    try:
        closes = df["close"].astype(float)
        if len(closes) >= 2 and closes.iloc[-2] > 0:
            ret1 = closes.iloc[-1] / closes.iloc[-2] - 1.0
            volr = sig.indicators.get("vol_ratio")
            if ret1 >= 0.03 and volr and volr >= 1.5:
                strength += 1.5
                reasons.append(f"+{ret1*100:.1f}% day on {volr:.1f}x volume")
            elif ret1 >= 0.05:
                strength += 1.0
                reasons.append(f"+{ret1*100:.1f}% day (thrust)")
    except Exception:
        pass

    if strength <= 0:
        return None
    return {"strength": round(strength, 2), "reasons": reasons}


def recently_sent_symbols(days: int = SIGNAL_COOLDOWN_DAYS) -> set[str]:
    """Symbols that fired a BUY signal in the last `days` calendar days
    (excludes today). Used by the cooldown gate to stop the same name from
    repeating day after day unless it has a fresh catalyst."""
    out: set[str] = set()
    today = datetime.now().date()
    for i in range(1, days + 1):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        p = LOGS_DIR / f"signals_sent_{d}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("sendable"):
                    out.add((rec.get("symbol") or "").upper())
            except Exception:
                continue
    return out


def sector_counts_for(symbols) -> dict:
    """Count how many of the given symbols map to each sector ETF."""
    counts: dict[str, int] = {}
    for s in symbols:
        sec = filters.sector_etf_for((s or "").upper())
        if sec:
            counts[sec] = counts.get(sec, 0) + 1
    return counts


def append_position_review_log(record: dict, session_name: str):
    today = datetime.now().strftime("%Y%m%d")
    path = LOGS_DIR / f"position_reviews_{today}.jsonl"
    row = {
        **record,
        "session": session_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _safe_float(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        if math.isnan(v):
            return default
        return v
    except Exception:
        return default


def _analyze_open_position(client: IBKRClient, pos: dict, market: dict,
                           news_events: list[dict]) -> dict:
    sym = (pos.get("symbol") or "").upper()
    qty = _safe_float(pos.get("quantity") or pos.get("position") or pos.get("qty"))
    avg_cost = _safe_float(pos.get("avg_cost"))
    market_price = _safe_float(pos.get("market_price"))

    review = {
        "symbol": sym,
        "quantity": qty,
        "avg_cost": avg_cost,
        "market_price": market_price,
        "pnl_pct": None,
        "recommendation": "REVIEW ERROR",
        "reasons": [],
        "indicators": {},
    }
    if not sym:
        review["reasons"].append("missing symbol from IBKR portfolio row")
        return review

    bars = client.get_historical_bars(sym, "1 Y", "1 day", "CAD")
    df = pd.DataFrame(bars)
    if df.empty:
        review["recommendation"] = "HOLD / DATA LIMITED"
        review["reasons"].append("no historical bars available")
        return review
    df.columns = [c.lower() for c in df.columns]
    df = add_all(df)
    snap = latest_snapshot(df)
    close = _safe_float(market_price or snap.get("close"))
    review["market_price"] = close
    review["indicators"] = {
        "close": close,
        "ema_20": snap.get("ema_20"),
        "ema_50": snap.get("ema_50"),
        "ema_200": snap.get("ema_200"),
        "rsi_14": snap.get("rsi_14"),
        "macd": snap.get("macd"),
        "macd_signal": snap.get("macd_signal"),
        "trend": snap.get("trend"),
    }

    if avg_cost > 0 and close > 0:
        review["pnl_pct"] = (close - avg_cost) / avg_cost

    reasons = []
    bearish_points = 0
    trend = snap.get("trend")
    ema20 = _safe_float(snap.get("ema_20"))
    ema50 = _safe_float(snap.get("ema_50"))
    rsi = _safe_float(snap.get("rsi_14"))
    macd = _safe_float(snap.get("macd"))
    macd_signal = _safe_float(snap.get("macd_signal"))
    pnl_pct = review["pnl_pct"]

    if pnl_pct is not None:
        if pnl_pct <= -0.025:
            bearish_points += 1
            reasons.append(f"down {pnl_pct*100:.1f}% vs avg cost")
        elif pnl_pct >= 0.05:
            reasons.append(f"up {pnl_pct*100:.1f}% vs avg cost")

    if trend == "bearish":
        bearish_points += 2
        reasons.append("bearish daily trend")
    elif trend == "bullish":
        reasons.append("bullish daily trend")

    if close > 0 and ema50 > 0 and close < ema50:
        bearish_points += 1
        reasons.append(f"below EMA-50 ({ema50:.2f})")
    if macd < macd_signal:
        bearish_points += 1
        reasons.append("MACD below signal")
    if snap.get("macd_bear_cross"):
        bearish_points += 1
        reasons.append("recent MACD bear cross")
    if snap.get("death_cross"):
        bearish_points += 2
        reasons.append("death cross")
    if rsi >= 70:
        reasons.append(f"RSI overbought ({rsi:.1f})")
    elif rsi <= 35:
        bearish_points += 1
        reasons.append(f"RSI weak ({rsi:.1f})")

    oil_ok, oil_why = filters.oil_gate_for_energy(sym, market.get("wti_pct"))
    if not oil_ok:
        bearish_points += 1
        reasons.append(oil_why)

    sector_ok, sector_penalty, sector_why = filters.sector_strength_ok(
        sym, market.get("sector_etf_pct")
    )
    if not sector_ok:
        bearish_points += 1
        reasons.append(sector_why)
    elif sector_penalty and sector_why:
        reasons.append(sector_why)

    news_delta, news_headline = filters.news_sentiment(news_events or [], sym)
    if news_delta < 0:
        bearish_points += 2
        reasons.append(f"bearish news: {news_headline}")
    elif news_delta > 0:
        reasons.append(f"bullish news: {news_headline}")

    if news_delta < 0 or snap.get("death_cross") or (
        pnl_pct is not None and pnl_pct <= -0.025 and close < ema50 and macd < macd_signal
    ) or (trend == "bearish" and (pnl_pct or 0) < 0):
        rec = "EXIT WATCH"
    elif pnl_pct is not None and pnl_pct >= 0.05 and (
        rsi >= 68 or macd < macd_signal or (ema20 > 0 and close < ema20)
    ):
        rec = "TAKE PROFIT WATCH"
    elif bearish_points >= 2:
        rec = "CAUTION / TIGHTEN STOP"
    else:
        rec = "HOLD / MONITOR"

    review["recommendation"] = rec
    review["reasons"] = reasons[:6] or ["no major warning from daily review"]
    return review


def review_existing_positions(client: IBKRClient, market: dict,
                              news_events: list[dict], session_name: str,
                              *, send_summary: bool = True) -> tuple[set[str], list[dict]]:
    try:
        positions = client.get_portfolio() or []
    except Exception as e:
        print(f"Portfolio review error: {e}")
        return set(), []

    owned_symbols = {
        (p.get("symbol") or "").upper()
        for p in positions
        if _safe_float(p.get("quantity") or p.get("position") or p.get("qty")) != 0
    }
    if not positions:
        return set(), []

    reviews = []
    for pos in positions:
        if (pos.get("symbol") or "").upper() not in owned_symbols:
            continue
        try:
            review = _analyze_open_position(client, pos, market, news_events)
        except Exception as e:
            review = {
                "symbol": (pos.get("symbol") or "").upper(),
                "recommendation": "REVIEW ERROR",
                "reasons": [str(e)],
            }
        append_position_review_log(review, session_name)
        reviews.append(review)

    if send_summary and reviews:
        lines = []
        for r in reviews[:12]:
            pnl = r.get("pnl_pct")
            pnl_s = "n/a" if pnl is None else f"{pnl*100:+.1f}%"
            reasons = html.escape("; ".join((r.get("reasons") or [])[:2]), quote=False)
            lines.append(
                f"{html.escape(str(r.get('symbol')), quote=False)}: {r.get('recommendation')} ({pnl_s})"
                + (f" - {reasons}" if reasons else "")
            )
        if len(reviews) > 12:
            lines.append(f"...and {len(reviews) - 12} more positions logged.")
        send_status(
            f"Portfolio review: {len(reviews)} open position(s)",
            "Owned tickers are excluded from new BUY signals.\n\n" + "\n".join(lines),
            icon="PORTFOLIO",
        )
    return owned_symbols, reviews


# ---------------------------------------------------------------
# Position-aware follow-up
# ---------------------------------------------------------------
def position_followup_alerts(client: IBKRClient, prior_signals: list[dict]):
    """For each open position, check vs prior signals → TP/SL alerts."""
    try:
        positions = client.get_portfolio()
    except Exception:
        return

    if not positions:
        return

    sig_by_symbol = {s.get("symbol"): s for s in prior_signals if s.get("sendable")}

    for pos in positions:
        sym = pos.get("symbol")
        if not sym or sym not in sig_by_symbol:
            continue
        signaled = sig_by_symbol[sym]
        entry = signaled.get("entry_price") or signaled.get("entry") or 0
        if entry <= 0:
            continue

        try:
            current = pos.get("market_price") or pos.get("avg_cost") or 0
            current = float(current)
        except Exception:
            continue

        pnl_pct = (current - entry) / entry if entry else 0.0
        if pnl_pct >= 0.05:
            send_status(f"📊 TP zone: {sym}",
                        f"Up {pnl_pct*100:.1f}% from signal entry ${entry:.2f}. "
                        f"Currently ${current:.2f}. Consider taking profit in IBKR mobile.",
                        icon="📊")
        elif pnl_pct <= -0.025:
            send_status(f"🟡 SL zone: {sym}",
                        f"Down {pnl_pct*100:.1f}% from signal entry ${entry:.2f}. "
                        f"Currently ${current:.2f}. Consider cutting in IBKR mobile.",
                        icon="🟡")


# ---------------------------------------------------------------
# Options signal scan (called after equity signals within a session)
# ---------------------------------------------------------------
def option_browser_names(bars_by_symbol: dict, news_events: list,
                         xic_bars: pd.DataFrame | None = None) -> dict[str, list[str]]:
    """Allocate public pages by strict direction, without approving any contract."""
    from options_signals import score_options_signal
    pools = {'CALL': [], 'PUT': []}
    for symbol, bars in bars_by_symbol.items():
        try:
            sig = score_options_signal(symbol, bars, currency='CAD', news_events=news_events,
                                       xic_bars=xic_bars, prefer_cad=True, allow_unpriced=True,
                                       skip_external_io=True)
            if sig:
                pools[sig.option_type].append((sig.confidence, symbol))
        except Exception:
            continue
    return {side: [symbol for _, symbol in sorted(pool, key=lambda item: -item[0])]
            for side, pool in pools.items()}


def last_closes(bars_by_symbol: dict) -> dict[str, float]:
    """Latest completed close per symbol, used only to choose strikes near spot."""
    closes = {}
    for symbol, bars in (bars_by_symbol or {}).items():
        try:
            close = float(bars['close'].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            continue
        if math.isfinite(close) and close > 0:
            closes[symbol.upper()] = close
    return closes


def interleave_names(first: list[str], second: list[str]) -> list[str]:
    return list(dict.fromkeys(name for pair in zip_longest(first, second)
                             for name in pair if name))


def select_option_cards(sigs: list, prior: list[dict]) -> list:
    """Keep per-side caps independent even when the opposite pool is empty."""
    counts = {side: sum(row.get('option_type', '').upper() == side for row in prior)
              for side in ('CALL', 'PUT')}
    rooms = {'CALL': max(0, OPTIONS_CALL_QUOTA - counts['CALL']),
             'PUT': max(0, OPTIONS_PUT_QUOTA - counts['PUT'])}
    chosen = []
    for sig in sigs:
        if rooms.get(sig.option_type, 0) > 0:
            chosen.append(sig)
            rooms[sig.option_type] -= 1
    return chosen[:max(0, OPTIONS_DAILY_QUOTA - len(prior))]


def options_coverage(prior: list[dict], delivered: list, pool: list, ideas: list,
                     evidence: dict, session: str, eligible_count: int):
    ideas = list({(sig.symbol, sig.option_type): sig for sig in ideas}.values())
    counts = {side: sum(row.get('option_type', '').upper() == side for row in prior) +
                    sum(sig.option_type == side for sig in delivered) for side in ('CALL', 'PUT')}
    audit = {'session': session, 'checked_at': datetime.now().isoformat(),
             'eligible_underlyings': eligible_count, 'browser_observed': sum(
                 item.get('status') == 'observed' for item in evidence.values()),
             'verified_pool': {side: sum(sig.option_type == side for sig in pool) for side in counts},
             'unpriced_ideas': {side: sum(sig.option_type == side for sig in ideas) for side in counts},
             'unpriced_names': {side: [sig.symbol for sig in ideas if sig.option_type == side] for side in counts},
             'candidate_rejections': {sig.symbol: {
                 'right': sig.option_type,
                 'collection_reason': evidence.get(sig.symbol, {}).get('collection_reason',
                     'not_browser_checked' if sig.symbol not in evidence else 'no_qualifying_native_contract'),
                 'contract_validation': evidence.get(sig.symbol, {}).get('contract_validation', {}),
                 'ibkr_collection_reason': evidence.get(sig.symbol, {}).get('ibkr_evidence', {}).get('collection_reason'),
                 'ibkr_contract_validation': evidence.get(sig.symbol, {}).get('ibkr_evidence', {}).get('contract_validation', {})
             } for sig in ideas},
             'browser_checked_symbols': [s for s, item in evidence.items() if item.get('status') == 'observed'],
             'quality_source': 'IBKR Paper or MX CDE contract quotes; not IBKR account funds',
             'ibkr_checked_symbols': [s for s, item in evidence.items()
                                      if item.get('source_kind') in ('ibkr', 'ibkr+mx')],
             'daily_delivered': counts, 'targets': {'CALL': OPTIONS_CALL_QUOTA, 'PUT': OPTIONS_PUT_QUOTA}}
    missing = [side for side in counts if counts[side] < audit['targets'][side]]
    if missing:
        body = (f"Daily qualified cards: CALL {counts['CALL']}/{OPTIONS_CALL_QUOTA}; "
                f"PUT {counts['PUT']}/{OPTIONS_PUT_QUOTA}. "
                f"Checked option quotes for {audit['browser_observed']} names "
                f"({len(audit['ibkr_checked_symbols'])} via IBKR) from {eligible_count} candidates. "
                f"Shortfall: {', '.join(missing)}. No additional verified, sendable contracts selected "
                "after direction, earnings, quote and market-window checks. Unchecked/unpriced names "
                "are not evidence that no setup exists. Unused slots are not filled from the other side.")
        body += (" Contract liquidity means the CDE option's bid/ask spread, open interest and volume, "
                 "not your account cash or the underlying share price. "
                 f"Unpriced PUT ideas: {', '.join(audit['unpriced_names']['PUT']) or 'none recorded'}.")
        send_status('Options coverage', body)
    try:
        (LOGS_DIR / f'options_coverage_{datetime.now():%Y%m%d}_{session}.json').write_text(
            json.dumps(audit, indent=2), encoding='utf-8')
    except OSError:
        print('[options] Coverage audit could not be saved.')


def run_options_scan(
    client: IBKRClient,
    regime: str,
    gold_tag: str,
    market: dict,
    news_events: list[dict],
    session_name: str,
    put_shortlist: list[str] | None = None,
    browser_evidence: dict | None = None,
    bars_by_symbol: dict | None = None,
    xic_bars: pd.DataFrame | None = None,
) -> int:
    """
    Score the optionable subset of the watchlist for directional CALL/PUT
    signals and send up to OPTIONS_DAILY_QUOTA cards per day.

    Canadian watchlist names do not require a US dual listing. A priced alert
    still requires an observed native CAD/CDE contract. Skip today's sent names.
    Returns number of options signals sent this call.

    Technical direction: bullish technical picture -> CALL, bearish -> PUT.
    Expiry: a listed native contract at 21-45 DTE.
    Strike: nearest qualifying quoted strike to spot; the card labels it ATM/ITM/OTM.
    Lots: user decides in IBKR mobile.
    """
    try:
        from options_signals import score_options_signal
        from telegram_notify import send_options_signal
    except ImportError as e:
        print(f"[options] import error (non-fatal): {e}")
        return 0

    # Quota check
    prior_opts = options_signals_sent_today()
    remaining = OPTIONS_DAILY_QUOTA - len(prior_opts)
    if remaining <= 0:
        print(f"[options] Daily quota of {OPTIONS_DAILY_QUOTA} already reached.")
        return 0

    already_sent_syms = {r.get("symbol", "").upper() for r in prior_opts}

    # US dual-listing is optional context, not Canadian contract eligibility.
    watchlist = load_watchlist()
    optionable = [sym for sym in watchlist if sym.upper() not in already_sent_syms]
    if not optionable:
        options_coverage(prior_opts, [], [], [], browser_evidence or {}, session_name, 0)
        return 0
    print(f"[options] Scanning {len(optionable)} optionable tickers (quota remaining: {remaining})...")

    # Fetch XIC bars once for Beta/Alpha computation (IBKR or Yahoo fallback)
    xic_df_opts: pd.DataFrame | None = xic_bars
    try:
        if xic_df_opts is None:
            xic_df_opts = fetch_bars(client, "XIC", HISTORY_DURATION, "1 day", "CAD")
        if xic_df_opts.empty:
            xic_df_opts = None
    except Exception:
        xic_df_opts = None
    bars_cache = dict(bars_by_symbol or {})
    def history(symbol):
        if symbol not in bars_cache:
            bars_cache[symbol] = fetch_bars(client, symbol, HISTORY_DURATION, '1 day', 'CAD')
        return bars_cache[symbol]
    if browser_evidence is None:
        for symbol in optionable:
            try:
                history(symbol)
            except Exception:
                continue
        directions = option_browser_names(bars_cache, news_events, xic_df_opts)
        option_directions = {s: 'PUT' for s in (put_shortlist or [])}
        option_directions.update({s: 'CALL' for s in directions['CALL']})
        option_directions.update({s: 'PUT' for s in directions['PUT']})
        browser_evidence = verify_shortlist(interleave_names(directions['CALL'],
                                           list(put_shortlist or []) + directions['PUT']),
                                           option_directions=option_directions,
                                           spots=last_closes(bars_cache))

    scored: list[tuple] = []  # (confidence, sig)
    underlying_ideas = []
    for sym in optionable:
        try:
            df = history(sym)
            if df.empty:
                continue
            sig = score_options_signal(
                sym, df, currency="CAD",
                news_events=news_events,
                xic_bars=xic_df_opts,
                ibkr_client=client,
                prefer_cad=True,
                browser_evidence=browser_evidence.get(sym), allow_unpriced=True,
            )
            if sig and not sig.sendable:
                underlying_ideas.append(sig)
            if sig and sig.sendable:
                scored.append((sig.confidence, sig))
                greek_note = ""
                if sig.delta is not None:
                    greek_note = f" Δ={sig.delta:+.2f}"
                if sig.beta is not None:
                    greek_note += f" β={sig.beta:.2f}"
                print(
                    f"  [options] Candidate: {sym} {sig.option_type} "
                    f"${sig.strike:.2f} exp {sig.expiry}{greek_note} conf={sig.confidence}"
                )
        except Exception as e:
            print(f"  [options] {sym} error: {e}")
            continue

    # v4.4 — Relaxed-PUT fallback. If the strict pass produced 0 PUT
    # candidates (typical of a strong bull tape), rerun the optionable
    # universe with relaxed_put=True so we surface the relative laggards
    # (negative alpha / below EMA-20 / weak RSI) as PUT candidates. This
    # keeps the user's 2 CALL + 2 PUT daily target reachable.
    # v5.6 — relaxed PUTs are no longer manufactured just to fill the PUT
    # quota (the one-month audit showed those were ~50% wrong). The relaxed
    # pass now only runs over names the full-watchlist deep-scan triage
    # called SHORT — i.e. there is independent AI evidence the name should
    # fall. With no SHORT verdicts, no relaxed PUTs are generated at all.
    strict_put_count = sum(1 for _, s in scored if (s.option_type or "").upper() == "PUT")
    relaxed_universe = [s for s in optionable
                        if put_shortlist and s.upper() in
                        {p.upper() for p in put_shortlist}]
    if strict_put_count == 0 and not relaxed_universe:
        print("[options] 0 strict PUTs and no deep-scan SHORT verdicts — "
              "not manufacturing PUTs (v5.6).")
    if strict_put_count == 0 and relaxed_universe:
        print(f"[options] Strict pass produced 0 PUTs. Relaxed-PUT pass over "
              f"deep-scan SHORTs: {relaxed_universe}")
        for sym in relaxed_universe:
            # Skip names that already produced a (CALL) signal in strict pass
            if any(s.symbol == sym for _, s in scored):
                continue
            try:
                df = history(sym)
                if df.empty:
                    continue
                sig = score_options_signal(
                    sym, df, currency="CAD",
                    news_events=news_events,
                    xic_bars=xic_df_opts,
                    relaxed_put=True,
                    ibkr_client=client,
                    prefer_cad=True,
                    browser_evidence=browser_evidence.get(sym), allow_unpriced=True,
                )
                if sig and not sig.sendable:
                    underlying_ideas.append(sig)
                if sig and sig.sendable and sig.option_type == "PUT":
                    scored.append((sig.confidence, sig))
                    greek_note = f" Δ={sig.delta:+.2f}" if sig.delta is not None else ""
                    if sig.alpha is not None:
                        greek_note += f" α={sig.alpha:+.1f}%"
                    print(
                        f"  [options/relaxed] PUT candidate: {sym} "
                        f"${sig.strike:.2f} exp {sig.expiry}{greek_note} conf={sig.confidence}"
                    )
            except Exception as e:
                print(f"  [options/relaxed] {sym} error: {e}")
                continue

    if underlying_ideas:
        import html
        unique = sorted({(s.symbol, s.option_type): s for s in underlying_ideas}.values(),
                        key=lambda s: -s.confidence)
        calls = [s for s in unique if s.option_type == 'CALL'][:2]
        puts = [s for s in unique if s.option_type == 'PUT'][:2]
        ideas = [s for pair in zip_longest(calls, puts) for s in pair if s]
        lines = [f"{html.escape(s.symbol)}: {s.option_type} underlying bias, not a contract recommendation"
                 for s in ideas]
        send_status("Options watchlist", "\n".join(lines) +
                    "\nNo timestamped liquid Canadian contract verified for these names. "
                    "No strike, premium or expiry is recommended.")
    if not scored:
        print("[options] No directional setups found.")
        options_coverage(prior_opts, [], [], underlying_ideas, browser_evidence, session_name, len(optionable))
        return 0

    # Sort by confidence descending (pre-LLM order)
    scored.sort(key=lambda x: x[0], reverse=True)
    sigs = [s for _, s in scored]

    # v5.1 — Pre-fetch fundamentals for each options candidate before LLM call.
    # This is the same data the equity pipeline fetches via enrichment.enrich
    # (the "fundamentals" key) — we mirror it here for the options path so
    # DeepSeek can factor underlying FCF / leverage into its rationale.
    try:
        import fundamentals as _fund
        for s in sigs:
            try:
                fdata = _fund.fetch_fundamentals(s.symbol, exchange_suffix=".TO")
                if fdata and fdata.get("available"):
                    s.fundamentals = fdata
            except Exception:
                continue
    except ImportError:
        pass

    # --- DeepSeek Reasoner research pass (mirrors equity pipeline) ---
    # Converts each OptionsSignal to a plain dict, calls research_options_candidates(),
    # then writes the LLM rationale + rank back onto the Signal objects.
    try:
        import llm_researcher
        if llm_researcher.is_enabled() and sigs:
            llm_in = [
                {
                    "symbol":       s.symbol,
                    "option_type":  s.option_type,
                    "strike":       s.strike,
                    "expiry":       s.expiry,
                    "dte":          s.dte,
                    "spot":         s.spot,
                    "spot_reference": s.spot_reference,
                    "est_premium":  s.est_premium,
                    "delta":        s.delta,
                    "gamma":        s.gamma,
                    "implied_vol":  s.implied_vol,
                    "beta":         s.beta,
                    "alpha":        s.alpha,
                    "max_pain":     s.max_pain,
                    "put_wall":     s.put_wall,
                    "call_wall":    s.call_wall,
                    "pc_ratio":     s.pc_ratio,
                    "reasons":      s.reasons,
                    "warnings":     s.warnings,
                    "confidence":   s.confidence,
                    "fundamentals": s.fundamentals,
                }
                for s in sigs
            ]
            market_ctx = {"regime": regime, "gold_tag": gold_tag, **market}
            ranked = llm_researcher.research_options_candidates(llm_in, market_ctx)

            # Map LLM output back to Signal objects by (symbol, option_type)
            rank_map = {
                f"{(r.get('symbol') or '').upper()}_{(r.get('option_type') or '').upper()}": r
                for r in ranked
            }
            for s in sigs:
                key = f"{s.symbol.upper()}_{s.option_type.upper()}"
                info = rank_map.get(key, {})
                s.llm_rationale = info.get("llm_rationale", "")
                s.llm_concerns  = info.get("llm_concerns", "")
                s.llm_veto      = bool(info.get("llm_veto", False))
                s._llm_rank     = info.get("llm_rank", 99)
                # v5.1 — soft veto: drop 1 star (still sends with badge)
                if s.llm_veto and s.confidence > 1:
                    s.confidence -= 1

            # Re-sort by LLM rank (best first), falling back to confidence
            sigs.sort(key=lambda s: (getattr(s, "_llm_rank", 99), -s.confidence))
    except Exception as e:
        print(f"[options] LLM research error (non-fatal): {e}")

    call_sigs = [s for s in sigs if (s.option_type or "").upper() == "CALL"]
    put_sigs  = [s for s in sigs if (s.option_type or "").upper() == "PUT"]
    chosen = select_option_cards(sigs, prior_opts)

    # Re-rank chosen for sending order (best first)
    chosen.sort(key=lambda s: (getattr(s, "_llm_rank", 99), -s.confidence))

    sent = 0
    delivered = []
    for sig in chosen[:remaining]:
        if not filters.in_signal_window()[0]:
            print("[options] Dispatch window closed after research; priced cards skipped.")
            break
        if sig.currency == "CAD":
            from research_pipeline import validated_contract
            if not any(validated_contract(row, sig.symbol) and row.get("right") == sig.option_type and
                       row.get("expiry") == sig.expiry and float(row.get("strike", 0)) == sig.strike
                       for row in browser_evidence.get(sig.symbol, {}).get("contracts", [])):
                print(f"[options] {sig.symbol}: contract evidence expired before dispatch.")
                continue
        ok = send_options_signal(sig, regime=regime, gold_tag=gold_tag)
        if ok:
            append_options_signal_log(sig, session_name)
            sent += 1
            delivered.append(sig)
            llm_note = " [AI-ranked]" if sig.llm_rationale else ""
            print(f"  [options] SENT: {sig.symbol} {sig.option_type} ${sig.strike:.2f} {sig.expiry}{llm_note}")

    options_coverage(prior_opts, delivered, sigs, underlying_ideas, browser_evidence, session_name, len(optionable))
    print(f"  [options] Sent {sent} (calls={sum(1 for s in delivered if s.option_type=='CALL')}, "
          f"puts={sum(1 for s in delivered if s.option_type=='PUT')}). "
          f"Pool: {len(call_sigs)} CALL, {len(put_sigs)} PUT.")

    return sent


# ---------------------------------------------------------------
# Run a session
# ---------------------------------------------------------------
def _connect_optional_gateway() -> IBKRClient | None:
    default = "true" if os.getenv("REQUIRE_GATEWAY", "false").lower() == "true" else "false"
    if os.getenv("IBKR_GATEWAY_ENABLED", default).lower() != "true":
        print("[gateway] Disabled. Using independent market data and optional browser research.")
        return None
    if os.getenv("IBKR_GATEWAY_UP", "1") == "0":
        print("[gateway] Probe says Gateway is DOWN. Continuing on Yahoo fallback.")
        return None
    client = None
    try:
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        client = IBKRClient()
        client.connect()
        return client
    except Exception as e:
        print(f"[gateway] IBKR connect failed: {e}. Continuing on Yahoo fallback.")
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        return None


def run_session(session_name: str):
    cfg = SESSION_CONFIG.get(session_name)
    if cfg is None:
        print(f"Unknown session: {session_name}", file=sys.stderr)
        sys.exit(2)

    label = cfg["label"]
    print(f"=== {label} ===")

    # Chrome and Gateway are independent optional sources, never joint gates.
    try:
        browser_status = optional_browser_status()
    except Exception:
        browser_status = {"available": False, "status": "unavailable"}
    browser_note = format_browser_status(browser_status)
    print(browser_note)

    # 0. Pull any new news from the 7 Telegram channels since last check.
    #    Wrapped: a news fetch failure must never block the session.
    try:
        n_news = news_fetcher.run_fetch()
        if n_news:
            print(f"News: {n_news} new matched events processed")
    except Exception as e:
        print(f"News fetch error (non-fatal): {e}")

    # 0b. Pull supplemental RSS news (press releases / earnings / M&A).
    #     Uses the same matcher and writes to the same news_events log.
    try:
        n_rss = news_rss_fetcher.run_fetch()
        if n_rss:
            print(f"RSS: {n_rss} new matched events processed")
    except Exception as e:
        print(f"RSS fetch error (non-fatal): {e}")

    # 1. Determine regime + gold + market state
    regime, gold_tag, market = determine_regime()
    market["optional_browser"] = browser_status
    print(f"Regime: {regime} | {gold_tag}")
    market_text = format_market_context(market) + "\n\n" + browser_note
    print(market_text)
    try:
        snapshot_path = LOGS_DIR / f"market_snapshot_{datetime.now():%Y%m%d}_{session_name}.json"
        snapshot_path.write_text(json.dumps({"session": session_name, **market}, indent=2),
                                 encoding="utf-8")
    except Exception as e:
        print(f"Market snapshot log error (non-fatal): {e}")

    # 1a. Classification coverage audit (v5.5 auto-sync guard). Surfaces any
    #     watchlist name that has drifted out of the sector/tech maps so the
    #     gates can't silently weaken as the watchlist grows.
    try:
        cov = filters.audit_sector_coverage(load_watchlist(), TECH_NAMES, GOLD_NAMES)
        if cov["unmapped"]:
            print(f"[coverage] {len(cov['unmapped'])} name(s) with no sector-ETF gate "
                  f"(fail-open): {', '.join(cov['unmapped'])}")
        if cov["tech_not_in_sectormap"] or cov["gold_not_in_sectormap"]:
            print(f"[coverage][WARN] classification drift — "
                  f"tech missing from SECTOR_MAP: {cov['tech_not_in_sectormap']}; "
                  f"gold missing: {cov['gold_not_in_sectormap']}")
    except Exception as e:
        print(f"Coverage audit error (non-fatal): {e}")

    # 1.5. Pre-market only — scan watchlist news for M&A / earnings catalysts.
    # yfinance has GlobeNewswire/PR/TipRanks feeds for Canadian tickers that
    # the general Telegram channels miss. Sends a 📢 CATALYST card per hit.
    if session_name in ("premarket_us", "premarket_ca"):
        try:
            n_cat = premarket_scan.scan_and_alert_catalysts(hours_back=24)
            if n_cat:
                print(f"Catalyst alerts: {n_cat}")
        except Exception as e:
            print(f"Catalyst scan error (non-fatal): {e}")

    # 2. Pre-market sessions: just send heads-up + exit
    if not cfg["do_signals"]:
        if session_name == "premarket_us":
            msg = (f"🌅 <b>Pre-Market US</b>\n"
                   + market_text)
            send(msg)
        elif session_name == "premarket_ca":
            msg = (f"🍁 <b>Pre-Market CA</b>\n"
                   + market_text)
            send(msg)
        elif session_name == "session4":
            client = _connect_optional_gateway()
            try:
                prior = signals_sent_today()
                position_reviews = []
                if client is not None:
                    news_events = filters.load_news_events_today()
                    _, position_reviews = review_existing_positions(
                        client, market, news_events, session_name, send_summary=True
                    )
                portfolio_note = ("EOD portfolio review completed." if client is not None else
                                  "Gateway offline: live portfolio review unavailable. "
                                  "Holdings and exits must be checked in IBKR mobile.")
                send_session_summary(
                    label, regime=regime, gold_tag=gold_tag,
                    signals_count=len(prior),
                    positions_count=len(position_reviews) if client is not None else None,
                    extras=(f"{portfolio_note}\n"
                            f"Options signals today: {len(options_signals_sent_today())}.\n\n"
                            + market_text),
                )
            finally:
                if client is not None:
                    try: client.disconnect()
                    except Exception: pass
        return

    # 3. Signal-generating sessions: Session 1/2/3
    timeframe = cfg["tf"]
    rules     = TIMEFRAME_RULES[timeframe]

    # Quota check
    prior = signals_sent_today()
    quota_reached = len(prior) >= DAILY_SIGNAL_QUOTA

    remaining_today   = max(0, DAILY_SIGNAL_QUOTA - len(prior))
    remaining_session = min(SESSION_SIGNAL_QUOTA, remaining_today)

    # Canadian sessions continue with Yahoo when the Gateway is unavailable.
    require_gateway = os.getenv("REQUIRE_GATEWAY", "false").lower() == "true"
    client = _connect_optional_gateway()
    gateway_connected = client is not None

    if not gateway_connected and require_gateway:
        send_status("Required Gateway unavailable",
                    "REQUIRE_GATEWAY=true and Gateway unavailable. Aborting.",
                    icon="⚠️")
        return

    if not gateway_connected:
        send_status("Gateway offline — fallback mode",
                    f"{label} running on Yahoo data only. "
                    "Live holdings and spreads are unverified. Portfolio review "
                    "skipped; qualifying stock signals and the scheduled "
                    "Session 2 options scan will continue.",
                    icon="⚠️")

    try:
        # Position-aware review FIRST. Skipped when we have no IBKR client.
        news_events = filters.load_news_events_today()
        owned_symbols: set[str] = set()
        position_reviews: list[dict] = []
        if gateway_connected:
            owned_symbols, position_reviews = review_existing_positions(
                client, market, news_events, session_name, send_summary=True
            )

        if quota_reached:
            # v4.4 — Equity quota is full, but Session 2 still owes us the
            # options scan (independent quota). Run it, then return.
            opts_sent_q = 0
            if session_name == "session2":
                try:
                    opts_sent_q = run_options_scan(
                        client, regime, gold_tag, market, news_events, session_name
                    )
                except Exception as e:
                    print(f"[options] scan error (non-fatal): {e}")
            if opts_sent_q == 0:
                send_no_signal(
                    label,
                    f"Daily equity quota of {DAILY_SIGNAL_QUOTA} reached. "
                    + (f"Reviewed {len(position_reviews)} open position(s). " if gateway_connected else
                       "Live portfolio unavailable. ")
                    + f"Options scan: {opts_sent_q} sent.\n\n" + market_text
                )
            else:
                daily_opts_total = len(options_signals_sent_today())
                send_session_summary(
                    label, regime=regime, gold_tag=gold_tag,
                    signals_count=0,
                    positions_count=len(position_reviews) if gateway_connected else None,
                    extras=(
                        f"Equity quota full ({len(prior)}/{DAILY_SIGNAL_QUOTA}). "
                        f"Options signals today: {daily_opts_total}/{OPTIONS_DAILY_QUOTA}.\n\n"
                        + market_text
                    )
                )
            return

        # 5. Get the full watchlist. Regime/gold context is still passed into
        # the scorer, but nothing is skipped before analysis.
        watchlist = load_watchlist()
        hot       = watchlist
        print(f"Scan names: {len(hot)}/{len(watchlist)}")

        # v5.1 — Broad-scope news LLM filter (no-op without API key, runs only
        # on Mon premarket+S2, Wed S1+S2, Fri S1+S2). When active, identifies
        # watchlist tickers with bullish/bearish news catalysts and PROMOTES
        # the bullish picks to the front of the hot list so they're guaranteed
        # to land in the research pool.
        news_llm_result = {}
        try:
            import news_llm_filter
            market_ctx = {"regime": regime, "gold_tag": gold_tag, **market}
            news_llm_result = news_llm_filter.find_news_driven_candidates(
                market_ctx, session_name
            )
            if news_llm_result:
                bullish_syms = news_llm_filter.get_bullish_symbols(news_llm_result)
                if bullish_syms:
                    promoted = [s for s in bullish_syms if s in {h.upper() for h in hot}]
                    rest = [h for h in hot if h.upper() not in set(promoted)]
                    hot = promoted + rest
                    print(f"[news-LLM] Promoted {len(promoted)} bullish news picks: {promoted}")
                    summary = news_llm_result.get("summary", "")
                    if summary:
                        # Send a Telegram heads-up showing what the LLM picked
                        send_status(
                            f"🧠 News-LLM picks ({session_name})",
                            f"<b>Promoted:</b> {html.escape(', '.join(promoted), quote=False)}\n\n"
                            f"<i>{html.escape(str(summary), quote=False)}</i>",
                            icon="🧠",
                        )
        except Exception as e:
            print(f"[news-LLM] non-fatal error: {e}")

        # 6. Pull XIC bars once (relative-strength reference) — IBKR or Yahoo
        try:
            xic_df = fetch_bars(client, "XIC", HISTORY_DURATION, "1 day", "CAD")
            if xic_df.empty:
                xic_df = None
        except Exception:
            xic_df = None

        # 8. Score every hot name before sending anything. Quotas limit how
        # many alerts go out, not how much of the watchlist gets analyzed.
        prior_symbols = {
            (s.get("symbol") or "").upper()
            for s in prior
            if s.get("sendable")
        }
        candidates = []
        sig_by_sym: dict = {}  # v5.6 — every scored name, for deep-scan triage
        catalyst_pool = []   # v5.5 — names with a fresh catalyst that FAILED the
        #                      trend rubric; injected into the research pool so
        #                      DeepSeek can still consider them (news-driven lane)
        duplicate_candidates = 0
        owned_skipped = 0
        scored = 0
        errors = 0
        for sym in hot:
            if sym.upper() in owned_symbols:
                owned_skipped += 1
                print(f"  OWNED SKIPPED: {sym} already in IBKR portfolio")
                continue
            try:
                df = fetch_bars(client, sym, HISTORY_DURATION, "1 day", "CAD")
                if df.empty:
                    continue

                # Pull weekly bars for multi-TF check (best-effort, IBKR-only;
                # Yahoo daily-resampled-to-weekly is computed inline if needed)
                weekly_df = None
                if gateway_connected:
                    try:
                        wbars = client.get_historical_bars(sym, "1 Y", "1 week", "CAD")
                        weekly_df = pd.DataFrame(wbars)
                        if not weekly_df.empty:
                            weekly_df.columns = [c.lower() for c in weekly_df.columns]
                    except Exception:
                        weekly_df = None
                if weekly_df is None or weekly_df.empty:
                    # Resample 1y daily -> weekly when IBKR is unavailable
                    daily_year = df
                    if not daily_year.empty:
                        try:
                            wk = daily_year.copy()
                            wk.index = pd.to_datetime(wk.index)
                            weekly_df = wk.resample("W").agg({
                                "open": "first", "high": "max", "low": "min",
                                "close": "last", "volume": "sum",
                            }).dropna()
                        except Exception:
                            weekly_df = None

                # Pull live snapshot quote — IBKR only. Tradability gate degrades
                # to spread-unknown when offline; the gate then accepts on
                # historical-volume basis.
                snap_quote = None
                if gateway_connected:
                    try:
                        quotes = client.get_quotes([sym], "CAD")
                        snap_quote = quotes[0] if quotes else None
                    except Exception:
                        pass

                sig = score_symbol(
                    df, symbol=sym, timeframe=timeframe, currency="CAD",
                    regime=regime, gold_tag=gold_tag,
                    xic_df=xic_df, weekly_df=weekly_df,
                    wti_pct_change=market.get("wti_pct"),
                    sector_etf_pct_change=market.get("sector_etf_pct"),
                    vix_level=market.get("vix_level"),
                    news_events=news_events,
                    snapshot_quote=snap_quote,
                    market=market,
                    use_atr_exits=True,
                    enforce_time_window=True,
                )
                scored += 1

                # v5.5 — catalyst detection runs on EVERY name, pass/fail.
                cat = _detect_catalyst(sym, sig, df, news_events)
                if cat:
                    sig.indicators["_catalyst"] = cat

                # v5.6 — keep every scored name for the deep-scan triage
                sig_by_sym[sym.upper()] = (sig, df)

                if sig.sendable:
                    if sym.upper() in prior_symbols:
                        duplicate_candidates += 1
                        print(f"  CANDIDATE SKIPPED: {sym} already alerted today")
                    else:
                        candidates.append(sig)
                elif cat and not sig.hard_blockers and sym.upper() not in prior_symbols:
                    # Failed the trend rubric but has a fresh catalyst. Flag it
                    # news-driven and let DeepSeek be the gatekeeper downstream.
                    sig.indicators["_news_driven"] = True
                    catalyst_pool.append(sig)
                    print(f"  CATALYST: {sym} failed rubric but has catalyst "
                          f"(strength {cat['strength']}): {cat['reasons'][0] if cat['reasons'] else ''}")
            except Exception as e:
                print(f"  ERROR {sym}: {e}", file=sys.stderr)
                errors += 1
                continue

        candidates.sort(key=_rank_signal, reverse=True)
        print(
            f"Scored {scored}/{len(hot)} hot names. "
            f"Sendable candidates: {len(candidates)}"
            + (f", duplicates skipped: {duplicate_candidates}" if duplicate_candidates else "")
            + (f", owned skipped: {owned_skipped}" if owned_skipped else "")
            + (f", errors: {errors}" if errors else "")
        )

        # v5.0 — Pre-enrich top N candidates (Barchart, options, ownership,
        # insider) so the LLM researcher gets full context. We enrich up to
        # 3x the session quota to give the LLM a real shortlist to rank.
        research_pool_size = max(remaining_session * 3, 6)
        research_pool = candidates[:research_pool_size]

        # v5.5 — inject the strongest news-driven names (failed the trend rubric)
        # so DeepSeek evaluates them too. Capped to protect token cost.
        catalyst_pool.sort(
            key=lambda s: s.indicators.get("_catalyst", {}).get("strength", 0),
            reverse=True,
        )
        injected = catalyst_pool[:CATALYST_POOL_MAX]
        if injected:
            research_pool = research_pool + injected
            print(f"[catalyst] Injected {len(injected)} news-driven name(s) into "
                  f"research pool: {[s.symbol for s in injected]}")

        # v5.6 — full-watchlist DeepSeek triage. Every scored name (small or
        # large, rubric pass or fail) gets a factsheet — momentum, cash flow
        # quality, options put/call positioning, news — and DeepSeek calls a
        # direction on each. High-conviction LONGs join the research pool;
        # SHORTs steer the options PUT scan.
        triage_map: dict = {}
        try:
            factsheets = []
            for symu, (s, d) in sig_by_sym.items():
                try:
                    factsheets.append(
                        deep_scan.build_factsheet(symu, s, d, news_events))
                except Exception:
                    continue
            if factsheets:
                market_ctx_t = {"regime": regime, "gold_tag": gold_tag, **market}
                triage_map = deep_scan.run_triage(
                    factsheets, market_ctx_t, session_name)
            if triage_map:
                t_long = deep_scan.long_picks(triage_map)
                t_short = deep_scan.short_picks(triage_map)
                print(f"[deep-scan] Triage: {len(triage_map)} verdicts — "
                      f"LONG>=4: {t_long or 'none'} | SHORT>=4: {t_short or 'none'}")
                pool_syms = {s.symbol.upper() for s in research_pool}
                deep_added = []
                for symu in t_long:
                    if (symu in pool_syms or symu in prior_symbols
                            or symu in owned_symbols):
                        continue
                    pair = sig_by_sym.get(symu)
                    if not pair:
                        continue
                    s = pair[0]
                    if s.hard_blockers:
                        continue
                    # Same DeepSeek send_ok gate as the catalyst lane: the
                    # stage-2 researcher must explicitly approve before send.
                    s.indicators["_news_driven"] = True
                    s.indicators["_deep_pick"] = triage_map[symu]
                    research_pool.append(s)
                    deep_added.append(symu)
                if deep_added:
                    print(f"[deep-scan] Injected {len(deep_added)} triage LONG "
                          f"pick(s) into research pool: {deep_added}")
        except Exception as e:
            print(f"[deep-scan] non-fatal error: {e}")

        for sig in research_pool:
            try:
                ext = enrichment.enrich(sig.symbol, currency="CAD")
                sig.indicators["_external"] = ext
            except Exception as e:
                print(f"  enrichment error {sig.symbol}: {e}")
                sig.indicators["_external"] = None

        # v5.0 — Optional LLM research layer (no-op without LLM_API_KEY).
        # When configured, the LLM re-ranks the research_pool and adds
        # rationale per candidate.
        try:
            import llm_researcher
            if llm_researcher.is_enabled() and research_pool:
                llm_in = [{
                    "symbol": s.symbol,
                    "score": s.score,
                    "weighted_score": getattr(s, "weighted_score", 0),
                    "min_score_required": getattr(s, "min_score_required", 0),
                    "setup_type": getattr(s, "setup_type", ""),
                    "reasons": s.reasons,
                    "warnings": s.warnings,
                    "entry_price": s.entry_price,
                    "stop_loss": s.stop_loss,
                    "take_profit": s.take_profit,
                    "external": s.indicators.get("_external"),
                    # v5.5 — news-driven lane context for the gatekeeper
                    "news_driven": bool(s.indicators.get("_news_driven")),
                    "catalyst": "; ".join(
                        (s.indicators.get("_catalyst") or {}).get("reasons", [])
                    ) or (s.indicators.get("_deep_pick") or {}).get("reason", ""),
                    # v5.6 — first-pass triage verdict (full-watchlist scan)
                    "triage": (lambda t: (
                        f"{t['direction']} conviction {t['conviction']}/5 — {t['reason']}"
                        if t else ""
                    ))(triage_map.get(s.symbol.upper())),
                } for s in research_pool]
                market_ctx = {"regime": regime, "gold_tag": gold_tag, **market}
                ranked = llm_researcher.research_candidates(llm_in, market_ctx)
                # Apply LLM ordering + rationale back onto Signal objects
                rank_by_sym = {(r.get("symbol") or "").upper(): r for r in ranked}
                for s in research_pool:
                    info = rank_by_sym.get(s.symbol.upper(), {})
                    s.indicators["_llm_rank"]      = info.get("llm_rank", 99)
                    s.indicators["_llm_rationale"] = info.get("llm_rationale", "")
                    s.indicators["_llm_concerns"]  = info.get("llm_concerns", "")
                    s.indicators["_llm_veto"]      = bool(info.get("llm_veto", False))
                    # v5.5 — send_ok gates the news-driven lane. Technical
                    # candidates (already passed the rubric) default True;
                    # news-driven names fire ONLY if DeepSeek explicitly approves.
                    nd = bool(s.indicators.get("_news_driven"))
                    s.indicators["_llm_send_ok"] = bool(
                        info.get("llm_send_ok", (not nd))
                    )
                    # v5.1 — soft veto: drop 1 confidence (still sends with badge)
                    if s.indicators["_llm_veto"] and s.confidence > 1:
                        s.confidence -= 1
                research_pool.sort(key=lambda s: s.indicators.get("_llm_rank", 99))
                # Promote LLM-ranked pool to front of candidate list
                rest = [c for c in candidates if c not in research_pool]
                candidates = research_pool + rest
        except Exception as e:
            print(f"  LLM research error (non-fatal): {e}")

        # Browser evidence is supplemental to the completed DeepSeek ranking.
        # Interleave SHORT ideas so the small browser budget does not exclude PUTs.
        long_names = [s.symbol for s in research_pool if not s.hard_blockers]
        short_names = deep_scan.short_picks(triage_map) if triage_map and session_name == "session2" else []
        options_bars = {symbol: pair[1] for symbol, pair in sig_by_sym.items()}
        if session_name == 'session2':
            previews = option_browser_names(options_bars, news_events, xic_df)
            long_names = list(dict.fromkeys(long_names + previews['CALL']))
            short_names = list(dict.fromkeys(short_names + previews['PUT']))
        browser_names = interleave_names(long_names, short_names)
        option_directions = None
        if session_name == 'session2':
            option_directions = {s: 'PUT' for s in short_names}
            option_directions.update({s: 'CALL' for s in previews['CALL']})
            option_directions.update({s: 'PUT' for s in previews['PUT']})
        browser_evidence = verify_shortlist(browser_names, option_directions=option_directions,
                                            spots=last_closes(options_bars) if option_directions else None)
        if browser_evidence:
            evidence_path = LOGS_DIR / f"browser_research_{datetime.now().strftime('%Y-%m-%d')}_{session_name}.json"
            try:
                evidence_path.write_text(json.dumps(browser_evidence, ensure_ascii=True, indent=2), encoding="utf-8")
            except OSError:
                print("[browser] Could not persist public evidence; analysis continues.")

        # 8b. Send only the highest-ranked candidates that fit the quotas.
        # v5.5 — selection now applies three diversity controls on top of the
        # quota: (1) news-driven names fire only with DeepSeek's send_ok,
        # (2) per-sector daily cap, (3) repeat cooldown (waived for fresh
        # catalysts). Counts seed from signals already sent earlier today.
        sent_this_session = 0
        cooldown_syms = recently_sent_symbols(SIGNAL_COOLDOWN_DAYS)
        sector_sent = sector_counts_for(
            (s.get("symbol") or "") for s in prior if s.get("sendable")
        )
        for sig in candidates:
            if sent_this_session >= remaining_session:
                break
            sym = sig.symbol
            if sig.hard_blockers:
                print(f"  BLOCKED: {sym} - mandatory filters failed")
                continue
            in_window, window_reason = filters.in_signal_window()
            if not in_window:
                print(f"[signals] Dispatch stopped after research: {window_reason}")
                break
            is_nd        = bool(sig.indicators.get("_news_driven"))
            has_catalyst = bool(sig.indicators.get("_catalyst"))

            # (1) News-driven gate — these failed the trend rubric, so they
            # only fire when DeepSeek explicitly approved (send_ok) and did
            # not veto. Then synthesize BUY levels from close + ATR.
            if is_nd:
                if not sig.indicators.get("_llm_send_ok", False) or sig.indicators.get("_llm_veto"):
                    print(f"  CATALYST SKIPPED: {sym} — DeepSeek did not approve (send_ok/veto)")
                    continue
                close = sig.indicators.get("close")
                atr_v = sig.indicators.get("atr_14")
                if not close or close <= 0:
                    continue
                sig.action = "BUY"
                sig.entry_price = round(close, 2)
                sig.stop_loss, sig.take_profit = filters.atr_based_exits(
                    close, atr_v, sl_atr_mult=2.0, tp_atr_mult=3.0
                )
                sig.qty = max(1, int(4000 / close))
                sig.sendable = True

            # (2) Cooldown — skip a name that fired in the last N days unless it
            # has a fresh catalyst worth re-flagging.
            if sym.upper() in cooldown_syms and not has_catalyst:
                print(f"  COOLDOWN SKIPPED: {sym} (fired within {SIGNAL_COOLDOWN_DAYS}d, no fresh catalyst)")
                continue

            # (3) Sector diversity cap.
            sec = filters.sector_etf_for(sym.upper())
            if sec and sector_sent.get(sec, 0) >= MAX_PER_SECTOR_PER_DAY:
                print(f"  SECTOR CAP SKIPPED: {sym} ({sec} already at {MAX_PER_SECTOR_PER_DAY}/day)")
                continue

            # External data was pre-fetched above for the research pool.
            ext = sig.indicators.get("_external")
            if ext is None and sig in research_pool:
                # research-pool member that failed enrichment — try once
                try:
                    ext = enrichment.enrich(sym, currency="CAD")
                except Exception:
                    ext = None
            elif ext is None:
                # not in research pool (low-rank candidate that got promoted by quota)
                try:
                    ext = enrichment.enrich(sym, currency="CAD")
                except Exception:
                    ext = None

            # Attach LLM rationale to the Telegram card if present
            llm_rat = sig.indicators.get("_llm_rationale")
            if llm_rat:
                if ext is None:
                    ext = {}
                ext["_llm_rationale"] = llm_rat
                ext["_llm_concerns"]  = sig.indicators.get("_llm_concerns", "")
                # v5.1 — propagate soft-veto flag for card badge rendering
                ext["_llm_veto"] = bool(sig.indicators.get("_llm_veto", False))

            # v5.1 — News-LLM origin tag for the card
            news_pick = None
            try:
                if news_llm_result:
                    import news_llm_filter
                    news_pick = news_llm_filter.get_pick_for_symbol(news_llm_result, sym)
            except Exception:
                news_pick = None
            if ext is None:
                ext = {}
            ext["_browser_note"] = evidence_note(browser_evidence.get(sym))
            ext["_data_basis"] = "Completed daily bars; delayed-data watchlist. Entry levels are references, not executable quotes."
            if browser_evidence.get(sym, {}).get("ai_review"):
                review = browser_evidence[sym]["ai_review"]
                ext["_browser_ai"] = review.get("summary", "") + " " + review.get("concerns", "")
            # v5.5 — attach the catalyst that pulled a news-driven name in
            if is_nd:
                cat = sig.indicators.get("_catalyst") or {}
                deep = sig.indicators.get("_deep_pick")
                ext["_catalyst"] = "; ".join(cat.get("reasons", [])) or (
                    (deep or {}).get("reason", ""))
                # v5.6 — distinguish full-watchlist triage picks from pure
                # news-catalyst picks on the card
                ext["_signal_origin"] = "DEEP-SCAN" if (deep and not cat) else "NEWS-DRIVEN"
            elif news_pick:
                ext["_news_llm_pick"] = news_pick
                ext["_signal_origin"] = "BOTH"   # rubric also passed → both agree
            else:
                # Pure technical pick (rubric only)
                ext.setdefault("_signal_origin", "TECHNICAL")

            ok = maybe_send(sig, regime=regime, gold_tag=gold_tag,
                            external=ext)
            if ok:
                append_signal_log({
                    "symbol": sig.symbol,
                    "action": sig.action,
                    "entry_price": sig.entry_price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "score": sig.score,
                    "weighted_score": sig.weighted_score,
                    "confidence": sig.confidence,
                    "timeframe": sig.timeframe,
                    "sendable": True,
                    "regime": regime,
                    "us_regime": market.get("us_regime"),
                    "data_mode": "IBKR + Yahoo" if gateway_connected else "Yahoo fallback",
                }, session_name)
                sent_this_session += 1
                if sec:
                    sector_sent[sec] = sector_sent.get(sec, 0) + 1
                origin = ext.get("_signal_origin", "technical") if is_nd else "technical"
                print(
                    f"  SENT [{origin}]: {sym} score={sig.score} "
                    f"weighted={sig.weighted_score:.2f} conf={sig.confidence}"
                    + (f" sector={sec}({sector_sent.get(sec)}/{MAX_PER_SECTOR_PER_DAY})" if sec else "")
                )

        # 8c. Options signal scan — Session 2 ONLY (per user request, v4.3).
        # Market is open and option chains have liquidity by 11:00 MDT.
        # Independent quota: 4/day (2 CALL + 2 PUT) regardless of equity quota.
        opts_sent = 0
        if session_name == "session2":
            try:
                opts_sent = run_options_scan(
                    client, regime, gold_tag, market, news_events, session_name,
                    put_shortlist=deep_scan.short_picks(triage_map) if triage_map else None,
                    browser_evidence=browser_evidence,
                    bars_by_symbol=options_bars, xic_bars=xic_df,
                )
            except Exception as e:
                print(f"[options] scan error (non-fatal): {e}")
        else:
            print(f"[options] Skipped (only runs in Session 2; current = {session_name})")

        # 9. Summary
        if sent_this_session == 0 and opts_sent == 0:
            send_no_signal(label,
                           f"Scanned {scored}/{len(hot)} names. "
                           f"{len(candidates)} fresh candidates after duplicate filter. "
                           f"{owned_skipped} owned ticker(s) skipped. "
                           + ("Live holdings unverified. " if not gateway_connected else "")
                           + "\n\n" + market_text)
        else:
            daily_opts_total = len(options_signals_sent_today())
            send_session_summary(
                label, regime=regime, gold_tag=gold_tag,
                signals_count=sent_this_session,
                positions_count=len(position_reviews) if gateway_connected else None,
                extras=(
                    f"Full scan: {scored}/{len(hot)} names scored. "
                    f"Candidates: {len(candidates)}. "
                    f"Owned skipped: {owned_skipped}. "
                    f"Daily equity total: {len(prior) + sent_this_session}/{DAILY_SIGNAL_QUOTA}. "
                    f"Options signals today: {daily_opts_total}/{OPTIONS_DAILY_QUOTA}. "
                    + ("Live holdings unverified." if not gateway_connected else "")
                    + "\n\n" + market_text
                )
            )
    finally:
        if client is not None:
            try: client.disconnect()
            except Exception: pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python session_runner.py {premarket_us|premarket_ca|session1|session2|session3|session4}")
        sys.exit(1)
    run_session(sys.argv[1])
