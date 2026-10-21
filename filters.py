"""
filters.py
----------
TSX-aware pre-scoring filters and contextual gates.

Used by session_runner.py BEFORE calling signals.score_symbol() on a
candidate, and inside signals.score_symbol() as additional checks.

Functions are designed to fail-open (return True / pass) when the
necessary data isn't available, so a transient network glitch on
Yahoo doesn't silently block all signals. Failures are logged.
"""
from __future__ import annotations
import math
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.request
import urllib.error

import pandas as pd
import numpy as np
from trading_calendar import signal_window, earnings_within

TRADING_DIR = Path(r"C:\Trading")
LOGS_DIR = TRADING_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------
# Tradability filters (run on live snapshot before scoring)
# ---------------------------------------------------------------
def passes_tradability(snap: dict,
                       min_price: float = 1.50,
                       max_spread_pct: float = 0.015,
                       min_volume: int = 30_000,
                       min_dollar_volume: float = 100_000.0) -> tuple[bool, list[str]]:
    """
    Reject illiquid / wide-spread / penny names that look good technically
    but cannot be reasonably filled at retail.

    `snap` is a dict from get_quotes / get_snapshot with keys:
        last, bid, ask, volume

    Returns (passes, reasons_failed).
    """
    fails = []
    last  = snap.get("last") or snap.get("close") or 0.0
    bid   = snap.get("bid")  or 0.0
    ask   = snap.get("ask")  or 0.0
    vol   = snap.get("volume") or 0

    try:
        last, bid, ask, vol = map(float, (last, bid, ask, vol))
        if not all(math.isfinite(v) for v in (last, bid, ask, vol)) or vol < 0:
            return False, ["Invalid price/volume data"]
    except (ValueError, TypeError):
        return False, ["Invalid price/volume data"]
    if bid > 0 and ask > 0 and ask < bid:
        fails.append("Crossed bid/ask quote")

    if last < min_price:
        fails.append(f"price ${last:.2f} below minimum ${min_price:.2f}")

    if vol < min_volume:
        fails.append(f"volume {vol:,} below minimum {min_volume:,}")

    dollar_vol = last * vol
    if dollar_vol < min_dollar_volume:
        fails.append(f"dollar volume ${dollar_vol:,.0f} below ${min_dollar_volume:,.0f}")

    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid if mid else 1.0
        if spread_pct > max_spread_pct:
            fails.append(f"spread {spread_pct*100:.2f}% > {max_spread_pct*100:.2f}%")

    return (len(fails) == 0, fails)


# ---------------------------------------------------------------
# Relative strength vs XIC (Canada index ETF)
# ---------------------------------------------------------------
def relative_strength_vs_xic(symbol_df: pd.DataFrame,
                             xic_df: pd.DataFrame,
                             lookback: int = 20) -> float | None:
    """
    Returns the symbol's % return minus XIC's % return over the last
    `lookback` bars. Positive = outperforming TSX, negative = lagging.

    Returns None if either series is too short.
    """
    if symbol_df is None or xic_df is None:
        return None
    if len(symbol_df) < lookback + 1 or len(xic_df) < lookback + 1:
        return None

    sym_ret = (symbol_df["close"].iloc[-1] / symbol_df["close"].iloc[-lookback - 1]) - 1
    xic_ret = (xic_df["close"].iloc[-1] / xic_df["close"].iloc[-lookback - 1]) - 1
    return float(sym_ret - xic_ret)


# ---------------------------------------------------------------
# Earnings blackout (3 trading days)
# ---------------------------------------------------------------
def has_earnings_within(symbol: str, days: int = 3,
                        currency: str = "CAD") -> tuple[bool, str | None]:
    """
    Check Yahoo Finance Canada earnings calendar for upcoming earnings.

    Returns (has_earnings_soon, ISO_date_or_None).
    Fails open (returns False) on any network/parse error so we don't
    silently block the whole watchlist when Yahoo hiccups.
    """
    # Prefer yfinance (more reliable than HTML scraping), with a small local cache.
    cache_path = LOGS_DIR / "earnings_cache.json"
    cache = {}
    try:
        if cache_path.exists():
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}

    now_utc = datetime.now(timezone.utc)
    key = f"{symbol.upper()}::{currency.upper()}"
    cached = cache.get(key) or {}
    cached_dt = None
    try:
        cached_dt = datetime.fromisoformat((cached.get("fetched_at") or "").replace("Z", "+00:00"))
    except Exception:
        cached_dt = None

    next_earn_str = cached.get("next_earnings")
    if next_earn_str and cached_dt and (now_utc - cached_dt) < timedelta(hours=24):
        try:
            ed = datetime.fromisoformat(next_earn_str).date()
            if earnings_within(ed, now_utc, days, currency):
                return (True, ed.isoformat())
            return (False, ed.isoformat())
        except Exception:
            pass

    # Fresh yfinance fetch (best-effort)
    try:
        import yfinance as yf
        from yf_symbols import to_yf_ticker  # v5.4.2 — correct Class-A/B + TSXV
        t = yf.Ticker(to_yf_ticker(symbol, currency=currency))
        cal = t.calendar
        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        else:
            try:
                ed = cal.loc["Earnings Date"].iloc[0]
            except Exception:
                ed = None
        if ed:
            if hasattr(ed, "__iter__") and not isinstance(ed, str):
                ed = list(ed)
                ed = ed[0] if ed else None
        if ed:
            # Normalize to YYYY-MM-DD
            ed_s = str(ed)[:10]
            try:
                ed_d = datetime.fromisoformat(ed_s).date()
            except Exception:
                ed_d = None
            cache[key] = {"next_earnings": ed_s, "fetched_at": now_utc.isoformat()}
            try:
                cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
            except Exception:
                pass
            if ed_d and earnings_within(ed_d, now_utc, days, currency):
                return (True, ed_d.isoformat())
            return (False, ed_s)
    except Exception:
        pass

    # Fallback: legacy Yahoo HTML string-search (fail-open on error)
    from yf_symbols import to_yf_ticker  # v5.4.2
    yf_sym = to_yf_ticker(symbol, currency=currency)
    url = f"https://finance.yahoo.com/calendar/earnings?symbol={yf_sym}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, Exception):
        return (False, None)

    today = datetime.now().date()
    for i in range(days * 3 + 5):
        d = today + timedelta(days=i)
        try:
            if not earnings_within(d, now_utc, days, currency):
                continue
        except Exception:
            return (False, None)
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
            stamp = d.strftime(fmt)
            if stamp in html:
                return (True, d.isoformat())
    return (False, None)


# ---------------------------------------------------------------
# ATR% volatility sanity (soft penalty)
# ---------------------------------------------------------------
def atr_volatility_penalty(close: float, atr: float | None,
                           low_atr_pct: float = 0.008,
                           high_atr_pct: float = 0.06) -> tuple[int, str | None]:
    """
    Softly penalize setups that are too low-vol (signals are mostly noise)
    or too high-vol (stops/targets become unreliable).

    Returns (penalty, note). Penalty is added to min_score (like sector penalty).
    """
    try:
        if atr is None or math.isnan(atr) or atr <= 0 or close <= 0:
            return (0, None)
        atr_pct = atr / close
        if atr_pct < low_atr_pct:
            return (1, f"ATR% {atr_pct*100:.2f}% too low — +1 min_score (avoid chop)")
        if atr_pct > high_atr_pct:
            return (1, f"ATR% {atr_pct*100:.2f}% too high — +1 min_score (avoid chaos)")
        return (0, None)
    except Exception:
        return (0, None)


# ---------------------------------------------------------------
# Resistance proximity (soft penalty)
# ---------------------------------------------------------------
def resistance_proximity_penalty(close: float, resistance: float | None,
                                 within_pct: float = 0.01) -> tuple[int, str | None]:
    """
    Soft penalty when price is too close to recent pivot resistance.
    This avoids sending BUY signals that immediately run into overhead supply.

    Returns (penalty, note). Penalty is added to min_score.
    """
    try:
        if resistance is None or math.isnan(resistance) or resistance <= 0 or close <= 0:
            return (0, None)
        # If already above resistance, no penalty (breakout scenario).
        if close >= resistance:
            return (0, None)
        dist = (resistance - close) / close
        if 0 <= dist <= within_pct:
            return (1, f"Near resistance {resistance:.2f} (only {dist*100:.1f}% overhead) — +1 min_score")
        return (0, None)
    except Exception:
        return (0, None)


# ---------------------------------------------------------------
# Oil correlation gate for Canadian energy names
# ---------------------------------------------------------------
# PRL removed v5.6 — Propel Holdings is a fintech lender, not energy
ENERGY_TICKERS = {"SU", "CNQ", "CVE", "ENB", "TOU", "ARX", "POU", "PSK", "EOG"}

def oil_gate_for_energy(symbol: str, wti_pct_change: float | None,
                        block_threshold: float = -0.025) -> tuple[bool, str | None]:
    """
    Block longs in Canadian energy names when WTI is down hard intraday.

    `wti_pct_change` is fractional (e.g. -0.03 = WTI down 3%).
    Defaults to NO block (pass) if data unavailable.
    """
    if symbol.upper() not in ENERGY_TICKERS:
        return (True, None)
    if wti_pct_change is None:
        return (True, None)
    if wti_pct_change <= block_threshold:
        return (False, f"WTI down {wti_pct_change*100:.1f}% — block energy longs")
    return (True, None)


# ---------------------------------------------------------------
# Sector ETF rotation overlay
# ---------------------------------------------------------------
# Sector-ETF rotation map. Keys MUST match watchlist.txt symbols (incl. dots
# like TECK.B / RCI.A / U.UN). Values are the TSX sector ETF used by the gate.
#
# Coverage is audited every session via audit_sector_coverage() — any watchlist
# name missing here is reported so the map can't silently drift out of sync as
# the watchlist grows (this is the v5.5 fix for ~1/3 of names bypassing gates).
#
# Valid TSX sector ETFs (verified resolvable): XEG, XGD, XFN, XIT, XMA, XUT,
# XRE, ZIN. NOTE: XCM.TO (communications) was DELISTED — telecom names are
# intentionally left unmapped (fail-open) rather than gated on a dead ticker.
SECTOR_MAP = {
    # Energy (XEG)
    "SU": "XEG", "CNQ": "XEG", "CVE": "XEG", "ENB": "XEG", "TOU": "XEG",
    "ARX": "XEG", "POU": "XEG", "PSK": "XEG", "EOG": "XEG",
    # Gold / Miners (XGD)
    "AEM": "XGD", "ABX": "XGD", "BTO": "XGD", "LUN": "XGD", "IVN": "XGD",
    "AII": "XGD", "RIC": "XGD", "CG": "XGD",
    # Banks / Financials (XFN)
    "RY": "XFN", "TD": "XFN", "BMO": "XFN", "CM": "XFN", "BNS": "XFN",
    "NA": "XFN", "EQB": "XFN", "MFC": "XFN", "SLF": "XFN", "GSY": "XFN",
    "FSV": "XFN", "PRL": "XFN",   # v5.6 — Propel Holdings (fintech lender)
    # Tech (XIT)
    "CSU": "XIT", "KXS": "XIT", "LMN": "XIT", "ENGH": "XIT", "MDA": "XIT",
    "CLS": "XIT", "SHOP": "XIT", "OTEX": "XIT", "LSPD": "XIT", "BB": "XIT",
    "TOI": "XIT", "DSG": "XIT", "DCBO": "XIT", "WELL": "XIT", "NDA": "XIT",
    # Materials (XMA)
    "NTR": "XMA", "TECK.B": "XMA", "MX": "XMA", "WFG": "XMA", "RUS": "XMA",
    "ASTL": "XMA", "CCO": "XMA", "NXE": "XMA", "DML": "XMA", "U.UN": "XMA",
    "UCU": "XMA", "RCK": "XMA",
    # Utilities / Renewables (XUT)
    "NPI": "XUT",
    # Real estate (XRE)
    "MEQ": "XRE",
    # Industrials (ZIN)
    "AC": "ZIN", "CNR": "ZIN", "CP": "ZIN", "HPS.A": "ZIN",
    # Intentionally UNMAPPED (no clean single TSX sector ETF) → fail-open:
    #   Telecom: BCE, T, RCI.A   (XCM delisted)
    #   Consumer: ATD, TOY, NWC, MFI, CTC, CTC.A
    #   Misc/data: TRI, TAL, RGSI, AIF
}

def sector_etf_for(symbol: str) -> str | None:
    return SECTOR_MAP.get(symbol.upper())


def audit_sector_coverage(watchlist: list[str],
                          tech_names: set[str] | None = None,
                          gold_names: set[str] | None = None) -> dict:
    """
    Auto-sync guard (v5.5). Compares the live watchlist against the
    classification maps so drift is caught the moment a new name is added.

    Returns a dict:
      unmapped            — watchlist names with no sector-ETF gate (fail-open).
                            Some of these are intentional (telecom/consumer);
                            shown for visibility, not necessarily an error.
      tech_not_in_sectormap — names in TECH_NAMES but missing from SECTOR_MAP
                            (a genuine inconsistency — they'd drop in bearish
                            regime but get no sector gate).
      gold_not_in_sectormap — same check for GOLD_NAMES.

    session_runner prints this at the top of every run; the WARN lines flag
    real inconsistencies vs. the merely-unmapped informational list.
    """
    mapped = {k.upper() for k in SECTOR_MAP}
    wl = [s.strip().upper() for s in watchlist if s and not s.startswith("#")]
    tech = {s.upper() for s in (tech_names or set())}
    gold = {s.upper() for s in (gold_names or set())}
    return {
        "unmapped": [s for s in wl if s not in mapped],
        "tech_not_in_sectormap": sorted(tech - mapped),
        "gold_not_in_sectormap": sorted(gold - mapped),
    }


def sector_strength_ok(symbol: str, sector_etf_pct_change: dict[str, float] | None,
                       soft_threshold: float = -0.005,
                       hard_threshold: float = -0.015) -> tuple[bool, int, str | None]:
    """
    Graduated sector gate (v4.4):
      sector_pct >= soft_threshold (-0.5%):  full pass, no penalty
      soft > pct > hard  (-0.5% to -1.5%):   SOFT — +1 to min_score required
      pct <= hard_threshold (-1.5%):         HARD block, signal suppressed

    Returns (allow_bool, score_penalty_int, reason_str_or_None).
    Pass if sector unknown or data unavailable.
    """
    sec = sector_etf_for(symbol)
    if sec is None or sector_etf_pct_change is None:
        return (True, 0, None)
    pct = sector_etf_pct_change.get(sec)
    if pct is None:
        return (True, 0, None)
    if pct <= hard_threshold:
        return (False, 0, f"sector ETF {sec} down {pct*100:.1f}% — hard block")
    if pct <= soft_threshold:
        return (True, 1, f"sector ETF {sec} down {pct*100:.1f}% — soft penalty (+1 min_score)")
    return (True, 0, None)


# ---------------------------------------------------------------
# Volatility regime gate
# ---------------------------------------------------------------
def volatility_regime_ok(vix_level: float | None,
                         pause_above: float = 25.0) -> tuple[bool, str | None]:
    """Pause new entries when VIX is in fear regime."""
    if vix_level is None:
        return (True, None)
    if vix_level > pause_above:
        return (False, f"VIX {vix_level:.1f} > {pause_above:.1f} — pause new entries")
    return (True, None)


# ---------------------------------------------------------------
# Time-of-day filter (TSX hours)
# ---------------------------------------------------------------
def in_signal_window(now_mdt: datetime | None = None) -> tuple[bool, str | None]:
    """
    Block signals in the first 30 min and last 15 min of TSX trading.
    TSX hours in MDT: 7:30 AM - 2:00 PM.
    """
    return signal_window(now_mdt)


# ---------------------------------------------------------------
# News blocker / booster
# ---------------------------------------------------------------
BEARISH_KEYWORDS = [
    "earnings miss", "missed estimates", "downgrade", "downgraded",
    "lawsuit", "investigation", "probe", "guidance cut", "lowered guidance",
    "halt", "halted", "restructuring", "layoffs", "ceo steps down",
    "fraud", "accounting irregularities", "going concern", "bankruptcy",
    "recall", "delist", "suspended",
]
BULLISH_KEYWORDS = [
    "earnings beat", "beat estimates", "upgrade", "upgraded",
    "raised guidance", "guidance raised", "buyback", "share repurchase",
    "acquisition", "merger", "spin-off", "approval", "fda approval",
    "contract win", "record results", "all-time high",
]

def news_sentiment(news_events: list[dict], symbol: str,
                   hours: int = 6) -> tuple[int, str | None]:
    """
    Walk news_events (loaded from logs/news_events_<date>.jsonl) and
    classify recent items mentioning `symbol`.

    Returns (delta, headline) where:
      delta = -1 (block, bearish news)
              0 (no recent news)
             +1 (boost score)
    """
    if not news_events:
        return (0, None)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    sym_upper = symbol.upper()

    for evt in reversed(news_events):  # newest first
        try:
            ts = datetime.fromisoformat(evt.get("timestamp", "").replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            break
        tickers = [t.upper() for t in evt.get("tickers", [])]
        if sym_upper not in tickers:
            continue

        # Only treat strong matches ("ticker" / "alias") as actionable for
        # blocking/boosting. Sector-keyword matches are informational and
        # are too noisy to gate individual tickers.
        match_types = evt.get("match_types") or {}
        mtype = (match_types.get(sym_upper) or "").lower()
        strong_match = mtype in ("ticker", "alias") or (mtype == "")

        text = (evt.get("headline", "") + " " + evt.get("body", "")).lower()

        # If the fetcher already computed sentiment, prefer it for strong matches.
        evt_sent = evt.get("sentiment")
        if strong_match and evt_sent in (-1, 1):
            return (int(evt_sent), evt.get("headline"))

        # Fallback keyword scan (kept for backward compatibility / older logs).
        for kw in BEARISH_KEYWORDS:
            if kw in text:
                return (-1, evt.get("headline")) if strong_match else (0, None)
        for kw in BULLISH_KEYWORDS:
            if kw in text:
                return (+1, evt.get("headline")) if strong_match else (0, None)
    return (0, None)


def load_news_events_today(logs_dir: str = "logs") -> list[dict]:
    """Load today's news events. Returns [] if file not present."""
    today = datetime.now().strftime("%Y%m%d")
    path = Path(logs_dir) / f"news_events_{today}.jsonl"
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


# ---------------------------------------------------------------
# ATR-based TP / SL (volatility-aware exits)
# ---------------------------------------------------------------
def atr_based_exits(close: float, atr: float,
                    sl_atr_mult: float = 1.5,
                    tp_atr_mult: float = 2.5) -> tuple[float, float]:
    """
    Returns (stop_loss, take_profit) using ATR multiples.
    Falls back to fixed % if ATR is missing.
    """
    if atr is None or math.isnan(atr) or atr <= 0:
        # Fallback: 2.5% / 5%
        return (round(close * 0.975, 2), round(close * 1.05, 2))
    sl = close - sl_atr_mult * atr
    tp = close + tp_atr_mult * atr
    return (round(sl, 2), round(tp, 2))


# ---------------------------------------------------------------
# Bank-specific RSI calibration
# ---------------------------------------------------------------
BANK_TICKERS = {"RY", "TD", "BMO", "CM", "BNS", "NA"}

def rsi_zone_for(symbol: str) -> tuple[float, float]:
    """Return (rsi_low, rsi_high) buyable zone, calibrated by sector."""
    if symbol.upper() in BANK_TICKERS:
        return (35.0, 60.0)  # banks have lower vol — wider buyable zone
    return (30.0, 55.0)       # default
