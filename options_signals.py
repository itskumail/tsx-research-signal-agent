"""
options_signals.py
------------------
Options signal generator for the Canadian watchlist.

CONTEXT
-------
The bot is a read-only analyst sending Telegram alerts. This module adds
a separate OPTIONS signal pipeline on top of the existing equity pipeline.

Canadian mode requires 250 completed daily bars, directional evidence and a
native timestamped liquid CAD/CDE contract from MX cells or IBKR Paper snapshots.
Listed expiries are 21-45 DTE. Missing contracts produce only unpriced
underlying ideas. No US strike conversion or invented Greeks are used.
See docs/RESEARCH_WORKFLOW.md for the current quote-quality rules.
"""
from __future__ import annotations

import math
import warnings
warnings.filterwarnings("ignore")

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from options_analyzer import DUAL_LIST_MAP
from research_pipeline import evidence_note

try:
    import mx_options  # CAD-native MX chain fetcher
except ImportError:
    mx_options = None  # graceful degradation if module missing


# ---------------------------------------------------------------
# Data class
# ---------------------------------------------------------------
@dataclass
class OptionsSignal:
    symbol: str             # TSX ticker (e.g. "RY")
    option_type: str        # "CALL" or "PUT"
    strike: float           # quoted strike nearest spot, chain currency (see `currency`)
    expiry: str             # "YYYY-MM-DD"
    dte: int                # calendar days to expiry
    spot: float             # underlying price/reference; see spot_reference
    max_pain: float | None
    put_wall: float | None
    call_wall: float | None
    pc_ratio: float | None
    est_premium: float | None  # bid/ask mid (or model price) in chain currency
    options_src: str           # source ticker (TSX sym for MX, US sym for US-ref)
    # v4.5 — currency-aware sourcing
    currency: str = "CAD"      # "CAD" if MX or FX-converted, "USD" if US-ref
    chain_source: str = "MX"   # "MX" | "FX-conv" | "US-ref"
    fx_rate: float | None = None  # USD->CAD used when chain_source="FX-conv"
    bid: float | None = None
    ask: float | None = None
    quote_time: str | None = None
    data_mode: str = "unverified"
    multiplier: float | None = None
    # Greeks (Black-Scholes from chain IV)
    delta: float | None = None   # Δ: price sensitivity per $1 underlying move
    gamma: float | None = None   # Γ: delta change per $1 underlying move
    implied_vol: float | None = None  # IV from chain (decimal, e.g. 0.25 = 25%)
    # Stock-level risk metrics (from IBKR bars vs XIC)
    beta: float | None = None    # β: volatility vs TSX (XIC) — 60-day
    alpha: float | None = None   # α: annualized excess return vs CAPM expectation
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: int = 1        # 1–5
    sendable: bool = False
    # LLM research layer (populated by llm_researcher.research_options_candidates)
    llm_rationale: str = ""    # DeepSeek Reasoner rationale for this candidate
    llm_concerns: str = ""     # DeepSeek concerns / red flags
    llm_veto: bool = False     # v5.1: True → soft veto (still sends with ⚠️ badge)
    fundamentals: dict | None = None  # v5.1: TTM FCF / yield / leverage block
    browser_note: str = ""
    browser_ai_review: dict | None = None
    spot_reference: str = "quote"  # "quote" or "previous_completed_close"


# ---------------------------------------------------------------
# Pure-math helpers (no scipy dependency)
# ---------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf — no scipy needed."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _black_scholes_greeks(
    spot: float,
    strike: float,
    dte_days: int,
    iv: float,
    option_type: str,
) -> dict:
    """
    Compute Black-Scholes Delta and Gamma.

    Parameters
    ----------
    spot        : current underlying price (USD)
    strike      : option strike (USD)
    dte_days    : calendar days to expiry
    iv          : implied volatility as decimal (e.g. 0.25 = 25%)
    option_type : "CALL" or "PUT"

    Delta (Δ)
      CALL: 0 to +1   (deep ITM → 1, ATM → 0.5, far OTM → 0)
      PUT:  -1 to 0   (deep ITM → -1, ATM → -0.5, far OTM → 0)

    Gamma (Γ)
      Always positive. Highest at ATM and at low DTE.
      High gamma = delta changes quickly as spot moves — more "acceleration".
      Low DTE + ATM = gamma spike (pin risk / volatility cluster).

    Uses Canadian risk-free proxy: 4.5 % annualized (short-term T-bill).

    Returns dict with 'delta', 'gamma' (both float) or None on error.
    """
    RISK_FREE = 0.045  # ~4.5 % Canada short rate
    T = max(dte_days, 1) / 365.0

    if spot <= 0 or strike <= 0 or iv <= 0.001:
        return {"delta": None, "gamma": None}
    try:
        d1 = (math.log(spot / strike) + (RISK_FREE + 0.5 * iv ** 2) * T) / (
            iv * math.sqrt(T)
        )
        gamma = _norm_pdf(d1) / (spot * iv * math.sqrt(T))
        delta = _norm_cdf(d1) if option_type.upper() == "CALL" else _norm_cdf(d1) - 1.0
        return {"delta": round(delta, 4), "gamma": round(gamma, 5)}
    except Exception:
        return {"delta": None, "gamma": None}


def _compute_beta_alpha(
    stock_bars: pd.DataFrame,
    xic_bars: pd.DataFrame,
    window: int = 60,
) -> tuple[float | None, float | None]:
    """
    60-day Beta and annualised Alpha vs XIC (TSX benchmark).

    Beta  (β) = Cov(stock, XIC) / Var(XIC)
      >1.0  → stock amplifies market moves (higher risk/reward)
      0.5–1 → defensive / less sensitive
      <0.5  → near-uncorrelated with broad market

    Alpha (α) = annualised excess return vs CAPM expectation.
      alpha_daily = stock_mean_return − [rf_daily + β × (xic_mean_return − rf_daily)]
      alpha_annual = alpha_daily × 252
      Positive → outperforming benchmark (supports CALL)
      Negative → underperforming benchmark (supports PUT)

    Returns (beta, alpha_pct_annualised) or (None, None) on insufficient data.
    """
    if xic_bars is None or xic_bars.empty or stock_bars is None or stock_bars.empty:
        return None, None
    try:
        s_cl = stock_bars["close"].dropna().tail(window + 1)
        x_cl = xic_bars["close"].dropna().tail(window + 1)
        if len(s_cl) < 10 or len(x_cl) < 10:
            return None, None

        s_ret = s_cl.pct_change().dropna().values
        x_ret = x_cl.pct_change().dropna().values
        n = min(len(s_ret), len(x_ret))
        if n < 10:
            return None, None
        s_ret, x_ret = s_ret[-n:], x_ret[-n:]

        cov_mat = np.cov(s_ret, x_ret)
        var_xic = cov_mat[1, 1]
        if var_xic == 0:
            return None, None

        beta = float(cov_mat[0, 1] / var_xic)
        rf_daily = 0.045 / 252
        s_mean = float(s_ret.mean())
        x_mean = float(x_ret.mean())
        alpha_daily = s_mean - (rf_daily + beta * (x_mean - rf_daily))
        alpha_annual = round(alpha_daily * 252 * 100, 2)  # annualised %
        return round(beta, 2), alpha_annual
    except Exception:
        return None, None


# ---------------------------------------------------------------
# Chain helpers
# ---------------------------------------------------------------
def _safe(val, default: float = 0.0) -> float:
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except Exception:
        return default


def _find_atm_call_strike(calls_df: pd.DataFrame, spot: float) -> float | None:
    """First call strike at or above spot (ATM call)."""
    if calls_df is None or calls_df.empty:
        return None
    above = calls_df[calls_df["strike"] >= spot * 0.99].sort_values("strike")
    if above.empty:
        above = calls_df.sort_values("strike")
    return float(above["strike"].iloc[0])


def _find_atm_put_strike(puts_df: pd.DataFrame, spot: float) -> float | None:
    """First put strike at or below spot (ATM put)."""
    if puts_df is None or puts_df.empty:
        return None
    below = puts_df[puts_df["strike"] <= spot * 1.01].sort_values(
        "strike", ascending=False
    )
    if below.empty:
        below = puts_df.sort_values("strike", ascending=False)
    return float(below["strike"].iloc[0])


def _est_premium(row: pd.Series) -> float | None:
    """Bid/ask mid, fallback to lastPrice."""
    try:
        bid = _safe(row.get("bid", 0))
        ask = _safe(row.get("ask", 0))
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 2)
        last = _safe(row.get("lastPrice", 0))
        return round(last, 2) if last > 0 else None
    except Exception:
        return None


def _pick_expiry(expiries: list[str]) -> tuple[str, int] | None:
    """Select the nearest listed expiry 21-45 calendar days away."""
    today = date.today()
    # Delayed-data trial: select a listed expiry with 21-45 calendar days left.
    eligible = []
    for expiry in expiries:
        try:
            dte = (date.fromisoformat(expiry) - today).days
            if 21 <= dte <= 45:
                eligible.append((expiry, dte))
        except (ValueError, TypeError):
            continue
    return min(eligible, key=lambda item: item[1]) if eligible else None




# ---------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------
def _resolve_chain_source(
    symbol: str,
    us_symbol: str | None,
    close: float,
    option_type: str,
    ibkr_client,
    prefer_cad: bool,
    browser_evidence: dict | None = None,
) -> dict | None:
    """
    Canadian mode uses validated native CAD/CDE MX or IBKR Paper evidence only.
    Legacy Gateway/US paths below are reachable only outside Canadian mode.

    Returns a normalized dict on success:
      {
        currency: "CAD" | "USD",
        chain_source: "MX" | "FX-conv" | "US-ref",
        spot: float,          # in chain currency
        strike: float,        # in chain currency
        expiry: str,          # "YYYY-MM-DD"
        dte: int,
        est_premium: float|None,
        delta: float|None,
        gamma: float|None,
        iv: float|None,
        fx_rate: float|None,  # USD->CAD used when FX-conv
        options_src: str,
      }
    Returns None when no source succeeds.
    """
    right = "C" if option_type == "CALL" else "P"

    # Native contracts are parsed from observed page cells, never model text.
    from research_pipeline import validated_contract
    rows = [validated_contract(row, symbol) for row in (browser_evidence or {}).get("contracts", [])]
    rows = [row for row in rows if row and row["right"] == option_type]
    if rows:
        row = min(rows, key=lambda r: (r["dte"], abs(r["strike"] - (r.get("spot") or close)), r["spread_pct"]))
        ibkr = row.get("source") == "IBKR CDE snapshot"
        reference = ("previous_completed_close" if ibkr or not row.get("spot")
                     else row.get("spot_reference", "quote"))
        # Greeks only when the quote source itself supplied them (IBKR); never modelled here.
        return {"currency": "CAD", "chain_source": "IBKR" if ibkr else "MX", "spot": row.get("spot") or close,
                "spot_reference": reference,
                "strike": row["strike"], "expiry": row["expiry"], "dte": row["dte"],
                "est_premium": row["mid"], "delta": row.get("delta") if ibkr else None, "gamma": None,
                "iv": row.get("implied_vol") if ibkr else None,
                "fx_rate": None, "options_src": symbol, "bid": row["bid"], "ask": row["ask"],
                "quote_time": row["quote_time"], "data_mode": row["data_mode"],
                "multiplier": row.get("multiplier")}

    # Canadian alerts do not substitute US contracts or FX-converted strikes.
    # Gateway remains a separately enabled legacy data path, not a prerequisite.
    if prefer_cad:
        return None

    # ---------- Path 1: IBKR MX (CAD-native) ----------
    if ibkr_client is not None and mx_options is not None:
        try:
            ib = getattr(ibkr_client, "ib", ibkr_client)
            meta = mx_options.fetch_chain_metadata(ib, symbol)
            if meta and meta["expiries"]:
                # Pick expiry using existing rule
                picked = _pick_expiry(meta["expiries"])
                if picked is not None:
                    exp_iso, dte = picked
                    exp_yyyymmdd = mx_options._denormalize_expiry(exp_iso)
                    # Use IBKR-derived spot (already CAD)
                    spot_cad = close
                    # Try strike nearest to spot, then nearest alternates
                    candidates = mx_options.nearest_alternate_strikes(
                        meta["strikes"], spot_cad, n=5)
                    for strike in candidates:
                        snap = mx_options.fetch_contract_snapshot(
                            ib, symbol, exp_yyyymmdd, strike, right,
                            meta["trading_class"], meta["multiplier"],
                            meta["exchange"])
                        if snap is None:
                            continue
                        # Prefer mid bid/ask, else model price, else close
                        prem = None
                        if snap["bid"] and snap["ask"]:
                            prem = round((snap["bid"] + snap["ask"]) / 2, 2)
                        elif snap["model_price"]:
                            prem = round(snap["model_price"], 2)
                        elif snap["close"]:
                            prem = round(snap["close"], 2)
                        return {
                            "currency": "CAD",
                            "chain_source": "MX",
                            "spot": spot_cad,
                            "strike": float(strike),
                            "expiry": exp_iso,
                            "dte": dte,
                            "est_premium": prem,
                            "delta": snap["delta"],
                            "gamma": snap["gamma"],
                            "iv": snap["iv"],
                            "fx_rate": None,
                            "options_src": symbol,  # TSX symbol itself
                        }
                    print(f"  [mx] {symbol}: no qualifiable strike for {exp_iso}")
        except Exception as e:
            print(f"  [mx] {symbol} resolver error (falling back to US): {e}")

    # ---------- Paths 2 & 3: US chain via yfinance ----------
    try:
        import yfinance as yf
        t = yf.Ticker(us_symbol)
        expiries_us = t.options or []
    except Exception:
        return None
    if not expiries_us:
        return None
    picked = _pick_expiry(expiries_us)
    if picked is None:
        return None
    exp_iso, dte = picked
    try:
        chain = t.option_chain(exp_iso)
        df_side = chain.calls if option_type == "CALL" else chain.puts
    except Exception:
        return None

    # Spot in USD from yfinance
    try:
        hist = t.history(period="2d")
        spot_usd = float(hist["Close"].iloc[-1])
    except Exception:
        spot_usd = close  # last-ditch

    if option_type == "CALL":
        strike_usd = _find_atm_call_strike(df_side, spot_usd)
    else:
        strike_usd = _find_atm_put_strike(df_side, spot_usd)
    if strike_usd is None:
        return None
    row = df_side[df_side["strike"] == strike_usd]
    row = row.iloc[0] if not row.empty else None
    prem_usd = _est_premium(row) if row is not None else None
    iv = _safe(row.get("impliedVolatility", 0)) if row is not None else 0.0
    iv = iv if iv > 0.01 else None

    # Black-Scholes greeks from US chain IV (caller will accept currency-agnostic)
    greeks = {"delta": None, "gamma": None}
    if iv and spot_usd > 0 and strike_usd > 0 and dte > 0:
        greeks = _black_scholes_greeks(spot_usd, strike_usd, dte, iv, option_type)

    # Path 2: FX-convert to CAD
    if prefer_cad:
        fx = None
        if ibkr_client is not None and mx_options is not None:
            try:
                ib = getattr(ibkr_client, "ib", ibkr_client)
                fx = mx_options.get_usdcad_rate(ib)
            except Exception:
                fx = None
        if fx is None:
            try:
                import yfinance as yf
                h = yf.Ticker("USDCAD=X").history(period="2d")
                fx = float(h["Close"].iloc[-1]) if not h.empty else None
            except Exception:
                fx = None
        if fx and fx > 0:
            return {
                "currency": "CAD",
                "chain_source": "FX-conv",
                "spot": round(spot_usd * fx, 2),
                "strike": round(strike_usd * fx, 2),
                "expiry": exp_iso,
                "dte": dte,
                "est_premium": (round(prem_usd * fx, 2) if prem_usd else None),
                "delta": greeks["delta"],   # delta/gamma currency-invariant
                "gamma": greeks["gamma"],
                "iv": iv,
                "fx_rate": round(fx, 4),
                "options_src": us_symbol,
            }
        # FX failed: drop to path 3

    # Path 3: USD reference (final fallback)
    return {
        "currency": "USD",
        "chain_source": "US-ref",
        "spot": spot_usd,
        "strike": float(strike_usd),
        "expiry": exp_iso,
        "dte": dte,
        "est_premium": prem_usd,
        "delta": greeks["delta"],
        "gamma": greeks["gamma"],
        "iv": iv,
        "fx_rate": None,
        "options_src": us_symbol,
    }


def score_options_signal(
    symbol: str,
    daily_bars: pd.DataFrame,
    currency: str = "CAD",
    news_events: list[dict] | None = None,
    xic_bars: pd.DataFrame | None = None,
    relaxed_put: bool = False,
    ibkr_client=None,
    prefer_cad: bool = True,
    browser_evidence: dict | None = None,
    allow_unpriced: bool = False,
    skip_external_io: bool = False,
) -> OptionsSignal | None:
    """
    Score a single TSX ticker for a directional options signal.

    Parameters
    ----------
    symbol      : TSX symbol (e.g. "RY")
    daily_bars  : OHLCV DataFrame from IBKR (≥20 bars, lowercase columns)
    currency    : "CAD" for TSX names
    news_events : today's events from filters.load_news_events_today()
    xic_bars    : XIC.TO daily bars (same period) — used for beta/alpha.
                  Pass None to skip beta/alpha computation.

    Returns OptionsSignal with sendable=True, or None on no setup / no data.
    """
    if (currency.upper() != 'CAD' or not prefer_cad) and DUAL_LIST_MAP.get(symbol) is None:
        return None
    if daily_bars is None or daily_bars.empty or len(daily_bars) < 250:
        return None

    import filters
    # Preview is only for browser allocation. Final scoring never skips earnings.
    earnings_soon, earnings_date = ((False, None) if skip_external_io else
                                    filters.has_earnings_within(symbol, days=3, currency=currency))
    if earnings_soon:
        return None
    last = daily_bars.iloc[-1]
    tradable, _ = filters.passes_tradability({"last": float(last["close"]), "volume": float(last["volume"])})
    if not tradable:
        return None

    us_symbol = DUAL_LIST_MAP.get(symbol)

    # --- Technical indicators from IBKR bars ---
    try:
        from indicators import add_all, latest_snapshot
    except ImportError:
        return None

    df = daily_bars.copy()
    if "ema_50" not in df.columns:
        df = add_all(df)
    snap = latest_snapshot(df)

    close     = _safe(snap.get("close", 0))
    trend     = snap.get("trend", "")
    death_x   = bool(snap.get("death_cross", False))
    golden_x  = bool(snap.get("golden_cross", False))
    rsi       = _safe(snap.get("rsi_14", 50), 50)
    macd      = _safe(snap.get("macd", 0))
    macd_sig  = _safe(snap.get("macd_signal", 0))
    macd_bull = bool(snap.get("macd_bull_cross", False))
    macd_bear = bool(snap.get("macd_bear_cross", False))
    ema_20    = _safe(snap.get("ema_20", close), close)
    ema_50    = _safe(snap.get("ema_50", close), close)
    ema_200   = _safe(snap.get("ema_200", close), close)
    bb_pct_b  = _safe(snap.get("bb_percent_b", 0.5), 0.5)

    if close <= 0:
        return None

    # --- Directional scoring ---
    bear_pts: int = 0
    bear_rsn: list[str] = []
    bull_pts: int = 0
    bull_rsn: list[str] = []

    # Bearish evidence (PUT)
    if trend == "bearish":
        bear_pts += 2
        bear_rsn.append("Trend bearish (close < EMA-50 < EMA-200)")
    if death_x:
        bear_pts += 2
        bear_rsn.append("Death cross — EMA-50 recently crossed below EMA-200")
    if macd < macd_sig:
        bear_pts += 1
        bear_rsn.append("MACD below signal line (momentum negative)")
    if macd_bear:
        bear_pts += 1
        bear_rsn.append("MACD bearish crossover")
    if rsi > 72:
        bear_pts += 1
        bear_rsn.append(f"RSI overbought ({rsi:.1f}) — mean-reversion candidate")
    if ema_50 > 0 and (close - ema_50) / ema_50 > 0.15:
        ext = (close - ema_50) / ema_50 * 100
        bear_pts += 1
        bear_rsn.append(f"Climax: {ext:.1f}% above EMA-50 — extended")
    if bb_pct_b >= 0.98:
        bear_pts += 1
        bear_rsn.append(f"Bollinger %B {bb_pct_b:.2f} — at upper band")

    # Bullish evidence (CALL)
    if trend == "bullish":
        bull_pts += 2
        bull_rsn.append("Trend bullish (close > EMA-50 > EMA-200)")
    if close > ema_50 and trend != "bearish":
        bull_pts += 1
        bull_rsn.append(f"Above EMA-50 ({ema_50:.2f})")
    if close > ema_200 and ema_200 > 0:
        bull_pts += 1
        bull_rsn.append(f"Above EMA-200 ({ema_200:.2f})")
    if macd > macd_sig:
        bull_pts += 1
        bull_rsn.append("MACD above signal line (momentum positive)")
    if macd_bull:
        bull_pts += 1
        bull_rsn.append("MACD bullish crossover")
    if golden_x:
        bull_pts += 1
        bull_rsn.append("Golden cross — EMA-50 recently crossed above EMA-200")
    if 45 <= rsi <= 65:
        bull_pts += 1
        bull_rsn.append(f"RSI in continuation zone ({rsi:.1f})")

    # Determine direction: clear majority (≥3 pts, must lead by ≥1)
    # v5.6 — a PUT additionally requires STRUCTURAL bearish evidence (broken
    # trend, death cross, or negative momentum). One-month live audit showed
    # PUTs built only from mean-reversion evidence (RSI>72 / climax / upper
    # band) against intact uptrends (ABX, CCO) were the single worst signal
    # class — 50% wrong. Overbought-but-trending names keep running.
    structural_bear = (trend == "bearish") or death_x or (macd < macd_sig)
    is_relaxed_put = False
    if bear_pts >= 3 and bear_pts > bull_pts and structural_bear:
        option_type = "PUT"
        dir_pts = bear_pts
        reasons = bear_rsn[:4]
    elif bull_pts >= 3 and bull_pts > bear_pts:
        option_type = "CALL"
        dir_pts = bull_pts
        reasons = bull_rsn[:4]
    elif relaxed_put:
        # v4.4 relaxed-PUT mode: in a strong bull tape, no name will pass the
        # strict bearish gate. To still surface PUT candidates for hedging /
        # mean-reversion, accept a PUT here if there is ANY relative-weakness
        # signal: bear evidence, below 20-EMA, or RSI sub-55.
        # v5.6 — never manufacture a PUT on a name still above its EMA-50:
        # "relative laggard in a bull tape" PUTs against intact uptrends were
        # the worst performers in the one-month live audit.
        if ema_50 > 0 and close > ema_50:
            return None
        rel_weak_reasons: list[str] = []
        if bear_pts >= 1:
            rel_weak_reasons.extend(bear_rsn[:2])
        if ema_20 > 0 and close < ema_20:
            rel_weak_reasons.append(f"Below EMA-20 ({ema_20:.2f}) — short-term weakness")
        if rsi < 55:
            rel_weak_reasons.append(f"RSI {rsi:.1f} — lagging momentum in bull tape")
        if not rel_weak_reasons:
            return None
        rel_weak_reasons.insert(0, "RELATIVE LAGGARD: weakest names in bull market often roll first")
        option_type = "PUT"
        dir_pts = 2  # cap at low directional pts for relaxed PUTs
        reasons = rel_weak_reasons[:4]
        is_relaxed_put = True
    else:
        return None

    # --- News gate: bearish news blocks CALL ---
    if news_events and option_type == "CALL":
        try:
            import filters
            nd, _ = filters.news_sentiment(news_events, symbol)
            if nd < 0:
                return None
        except Exception:
            pass

    # Native CAD contract evidence; no US/FX substitution in Canadian mode.
    chain_data = _resolve_chain_source(
        symbol=symbol,
        us_symbol=us_symbol,
        close=close,
        option_type=option_type,
        ibkr_client=ibkr_client,
        prefer_cad=prefer_cad,
        browser_evidence=browser_evidence,
    )
    unpriced = chain_data is None
    if unpriced and not allow_unpriced:
        return None
    if unpriced:
        chain_data = {"spot": close, "strike": 0, "expiry": "", "dte": 0,
                      "est_premium": None, "delta": None, "gamma": None, "iv": None,
                      "currency": "CAD", "chain_source": "unverified", "fx_rate": None,
                      "options_src": symbol, "spot_reference": "previous_completed_close"}

    spot         = chain_data["spot"]
    strike       = chain_data["strike"]
    chosen_str   = chain_data["expiry"]
    chosen_dte   = chain_data["dte"]
    premium      = chain_data["est_premium"]
    delta        = chain_data["delta"]
    gamma        = chain_data["gamma"]
    implied_vol  = chain_data["iv"]
    chain_ccy    = chain_data["currency"]
    chain_source = chain_data["chain_source"]
    fx_rate      = chain_data["fx_rate"]
    chain_src_sym = chain_data["options_src"]

    # --- Options context (max pain, walls, P/C) — still from US chain (cheap) ---
    # If chain currency is CAD via FX, scale walls/pain by fx_rate so the card
    # is internally consistent. If chain is MX, walls remain US-derived but
    # FX-scaled — they're context only, not used for entry.
    max_pain = put_wall = call_wall = pc_ratio = None
    try:
        from options_analyzer import analyze_options
        opt_ctx = analyze_options(symbol, currency="CAD") if chain_ccy != 'CAD' and not skip_external_io else {}
        if opt_ctx and chain_ccy != "CAD":
            max_pain  = opt_ctx.get("max_pain")
            put_wall  = opt_ctx.get("put_wall")
            call_wall = opt_ctx.get("call_wall")
            pc_ratio  = opt_ctx.get("pc_ratio")
            # Scale to CAD when we're showing a CAD card
            if chain_ccy == "CAD":
                # Use fx_rate if present (FX-conv path) or fetch one for MX path
                scale = fx_rate
                if scale is None and ibkr_client is not None and mx_options is not None:
                    try:
                        ib = getattr(ibkr_client, "ib", ibkr_client)
                        scale = mx_options.get_usdcad_rate(ib)
                    except Exception:
                        scale = None
                if scale is None:
                    try:
                        import yfinance as yf
                        h = yf.Ticker("USDCAD=X").history(period="2d")
                        scale = float(h["Close"].iloc[-1]) if not h.empty else None
                    except Exception:
                        scale = None
                if scale and scale > 0:
                    if max_pain:  max_pain  = round(max_pain  * scale, 2)
                    if put_wall:  put_wall  = round(put_wall  * scale, 2)
                    if call_wall: call_wall = round(call_wall * scale, 2)
                else:
                    # FX unavailable — drop walls rather than show misleading USD values
                    # on a CAD-labeled card. P/C ratio is dimensionless, keep it.
                    max_pain = put_wall = call_wall = None
    except Exception:
        pass

    # v5.6 — Put/Call positioning gate: don't send a PUT when the options
    # market is positioned bullish (low P/C ratio = traders own far more
    # calls than puts). Relaxed PUTs need at least neutral positioning;
    # strict PUTs lose a star against clearly bullish positioning.
    # Open interest does not identify trade direction or guaranteed support.
    pc_confidence_penalty = False

    # --- Beta and Alpha from IBKR bars vs XIC ---
    beta, alpha = _compute_beta_alpha(df, xic_bars)

    # --- Confidence (1–5 base from directional score) ---
    confidence = min(5, max(1, dir_pts - 1))
    if is_relaxed_put:
        confidence = min(confidence, 3)  # relaxed PUTs are softer setups
    if pc_confidence_penalty and confidence > 1:
        confidence -= 1
        sig_warnings_pc = f"P/C ratio {pc_ratio:.2f} — options market positioned bullish, against this PUT"
    else:
        sig_warnings_pc = None

    # Alpha bonus: +1 confidence if alpha confirms direction
    sig_warnings: list[str] = ["Canadian contract quote not verified; underlying watchlist only."] if unpriced else []
    if not earnings_date:
        sig_warnings.append("Earnings date unavailable; verify before acting")
    if sig_warnings_pc:
        sig_warnings.append(sig_warnings_pc)
    if alpha is not None:
        if option_type == "CALL" and alpha > 5.0:
            confidence = min(5, confidence + 1)
            reasons.append(f"Alpha +{alpha:.1f}% annualised (outperforming XIC — confirms CALL)")
        elif option_type == "PUT" and alpha < -5.0:
            confidence = min(5, confidence + 1)
            reasons.append(f"Alpha {alpha:.1f}% annualised (underperforming XIC — confirms PUT)")

    # Delta warnings
    if delta is not None:
        abs_delta = abs(delta)
        if abs_delta < 0.20:
            sig_warnings.append(
                f"Delta {delta:+.2f} — far OTM, option needs large move to profit"
            )
        elif abs_delta > 0.85:
            sig_warnings.append(
                f"Delta {delta:+.2f} — deep ITM, high cost / low leverage"
            )

    # Gamma risk warning
    if gamma is not None and gamma > 0.07 and chosen_dte <= 7:
        sig_warnings.append(
            f"Gamma {gamma:.4f} with {chosen_dte}d to expiry — high pin / gamma risk"
        )

    # Options context reasons
    if max_pain and spot > 0 and abs(spot - max_pain) / max_pain < 0.03:
        reasons.append(f"Near max pain ${max_pain:.2f} — watch pin risk near expiry")
    if option_type == "CALL" and put_wall:
        reasons.append(f"Put open-interest concentration at ${put_wall:.2f} (context only)")
    if option_type == "PUT" and call_wall:
        reasons.append(f"Call open-interest concentration at ${call_wall:.2f} (context only)")

    return OptionsSignal(
        symbol=symbol,
        option_type=option_type,
        strike=strike,
        expiry=chosen_str,
        dte=chosen_dte,
        spot=spot,
        max_pain=max_pain,
        put_wall=put_wall,
        call_wall=call_wall,
        pc_ratio=pc_ratio,
        est_premium=premium,
        options_src=chain_src_sym,
        currency=chain_ccy,
        chain_source=chain_source,
        fx_rate=fx_rate,
        bid=chain_data.get("bid"), ask=chain_data.get("ask"),
        quote_time=chain_data.get("quote_time"), data_mode=chain_data.get("data_mode", "unverified"),
        multiplier=chain_data.get("multiplier"),
        delta=delta,
        gamma=gamma,
        implied_vol=implied_vol,
        beta=beta,
        alpha=alpha,
        reasons=reasons[:5],
        warnings=sig_warnings,
        confidence=confidence,
        sendable=not unpriced and not skip_external_io,
        browser_note=evidence_note(browser_evidence),
        browser_ai_review=(browser_evidence or {}).get('ai_review'),
        spot_reference=chain_data.get("spot_reference", "quote"),
    )


# ---------------------------------------------------------------
# Interpretation helpers (for card display)
# ---------------------------------------------------------------
def _delta_label(delta: float, option_type: str) -> str:
    """Human-readable delta context."""
    abs_d = abs(delta)
    if abs_d < 0.20:
        return "far OTM — needs strong move"
    if abs_d < 0.35:
        return "OTM — moderate leverage"
    if abs_d <= 0.65:
        return "ATM — balanced risk/reward"
    if abs_d <= 0.85:
        return "ITM — higher cost, lower leverage"
    return "deep ITM — expensive, tracks stock closely"


def _moneyness_label(option_type: str, strike: float, spot: float) -> str:
    """Describe the quoted strike against spot instead of assuming ATM."""
    if not strike or not spot:
        return "moneyness unavailable"
    pct = (strike - spot) / spot * 100.0
    if abs(pct) <= 1.0:
        return "ATM"
    in_the_money = strike < spot if option_type.upper() == "CALL" else strike > spot
    return f"{abs(pct):.1f}% {'ITM' if in_the_money else 'OTM'}"


def _gamma_label(gamma: float, dte: int) -> str:
    """Human-readable gamma context."""
    if gamma > 0.07:
        return f"HIGH — delta shifts fast{'  ⚠️ pin risk' if dte <= 7 else ''}"
    if gamma > 0.03:
        return "moderate — watch near expiry"
    return "low — delta relatively stable"


def _beta_label(beta: float) -> str:
    if beta > 1.5:
        return "high volatility vs market — amplified swings"
    if beta > 1.0:
        return "market-plus volatility"
    if beta > 0.6:
        return "moderate — less volatile than market"
    return "low — near-uncorrelated with TSX"


def _alpha_label(alpha: float) -> str:
    if alpha > 10:
        return f"+{alpha:.1f}%/yr — strong outperformer"
    if alpha > 3:
        return f"+{alpha:.1f}%/yr — outperforming XIC"
    if alpha > -3:
        return f"{alpha:+.1f}%/yr — roughly market return"
    if alpha > -10:
        return f"{alpha:.1f}%/yr — underperforming XIC"
    return f"{alpha:.1f}%/yr — significant underperformer"


# ---------------------------------------------------------------
# Telegram card formatter
# ---------------------------------------------------------------
def format_options_card(
    sig: OptionsSignal,
    regime: str = "",
    gold_tag: str = "",
) -> str:
    """
    HTML Telegram card for an options signal.
    Distinctly styled vs equity BUY cards.

    v4.3 fix: every dynamic string is HTML-escaped before insertion.
    Earlier versions emitted raw '<' characters from PUT-side reasons such
    as "Trend bearish (close < EMA-50 < EMA-200)", which Telegram rejected
    as "Unsupported start tag" (HTTP 400). All CALL signals went through
    because their reasons used '>' instead, which Telegram tolerates.
    """
    import html as _html
    def _esc(s) -> str:
        return _html.escape(str(s), quote=False) if s is not None else ""

    type_emoji = "📈 CALL" if sig.option_type == "CALL" else "📉 PUT"
    stars = "★" * sig.confidence + "☆" * (5 - sig.confidence)

    # v4.5 — currency-aware price formatting
    ccy = (sig.currency or "CAD").upper()
    cs = "C$" if ccy == "CAD" else "US$"

    prem_line = (
        f"Quoted midpoint: ~<b>{cs}{sig.est_premium:.2f}</b>/share\n"
        if sig.est_premium else ""
    )
    if sig.est_premium and sig.multiplier:
        prem_line += f"Contract premium: ~{cs}{sig.est_premium * sig.multiplier:.2f} ({sig.multiplier:g} shares)\n"
    if sig.bid is not None and sig.ask is not None:
        prem_line += f"Bid/ask: {cs}{sig.bid:.2f} / {cs}{sig.ask:.2f}\n"
    prem_line += f"Data: {_esc(sig.data_mode)}; quote {_esc(sig.quote_time or 'time unavailable')}\n"

    # P/C ratio
    pc_note = ""
    if sig.pc_ratio is not None:
        pc_note = f"P/C OI ratio: {sig.pc_ratio:.2f} (context, not direction)\n"

    # Walls / max pain
    walls = ""
    if sig.max_pain:
        walls += f"Max pain:     {cs}{sig.max_pain:.2f}\n"
    if sig.put_wall:
        walls += f"Put OI concentration: {cs}{sig.put_wall:.2f}\n"
    if sig.call_wall:
        walls += f"Call OI concentration: {cs}{sig.call_wall:.2f}\n"

    # Greeks & Risk section (labels are static literals, no escaping needed)
    greeks_lines = []
    if sig.delta is not None:
        greeks_lines.append(
            f"  • <b>Delta (Δ):</b>  {sig.delta:+.3f}  — {_esc(_delta_label(sig.delta, sig.option_type))}"
        )
    if sig.gamma is not None:
        greeks_lines.append(
            f"  • <b>Gamma (Γ):</b>  {sig.gamma:.5f}  — {_esc(_gamma_label(sig.gamma, sig.dte))}"
        )
    if sig.implied_vol is not None:
        greeks_lines.append(f"  • <b>Impl. Vol:</b>   {sig.implied_vol*100:.1f}%")
    if sig.beta is not None:
        greeks_lines.append(
            f"  • <b>Beta (β):</b>   {sig.beta:.2f}  — {_esc(_beta_label(sig.beta))}"
        )
    if sig.alpha is not None:
        greeks_lines.append(
            f"  • <b>Alpha (α):</b>  {_esc(_alpha_label(sig.alpha))}"
        )

    greeks_section = ""
    if greeks_lines:
        greeks_section = "\n<b>Greeks &amp; Risk:</b>\n" + "\n".join(greeks_lines) + "\n"

    # Warnings — escape user-derived text
    warns_section = ""
    if sig.warnings:
        warns_section = "\n<b>⚠️ Warnings:</b>\n" + "\n".join(
            f"  • {_esc(w)}" for w in sig.warnings
        ) + "\n"

    # Reasons — escape (the bearish trend reason contains '<' which Telegram rejects)
    reasons_txt = "\n".join(f"  • {_esc(r)}" for r in sig.reasons) or "  • (technical direction)"

    regime_line = ""
    if regime:
        regime_line = f"Regime: {_esc(regime)}" + (f" | {_esc(gold_tag)}" if gold_tag else "") + "\n"

    # v5.1 — Fundamentals section (FCF / yield / leverage on underlying)
    fund_section = ""
    if sig.fundamentals and sig.fundamentals.get("available"):
        try:
            from fundamentals import format_fundamentals_block
            block = format_fundamentals_block(sig.fundamentals, currency=ccy)
            if block.strip():
                fund_section = f"\n<b>📊 Underlying Fundamentals:</b>\n{block}"
        except Exception:
            pass

    # LLM research section (only shown when DeepSeek Reasoner provided analysis)
    llm_section = ""
    if sig.llm_rationale:
        header = "🧠 AI Analysis (DeepSeek Reasoner)"
        if sig.llm_veto:
            header = "⚠️ AI Flagged — review carefully"
        llm_section = f"\n<b>{header}:</b>\n  • {_esc(sig.llm_rationale)}"
        if sig.llm_concerns:
            llm_section += f"\n  • <i>Risk: {_esc(sig.llm_concerns)}</i>"
        llm_section += "\n"

    # v4.5 — clickable verification links. Barchart serves CAD bid/ask
    # on TSX names via the .TO suffix; user taps to verify the live
    # spread before executing in IBKR mobile.
    barchart_url = f"https://www.barchart.com/stocks/quotes/{sig.symbol}.TO/options"
    tmx_url      = f"https://money.tmx.com/quote/{sig.symbol}/options"
    from urllib.parse import quote
    mx_url = 'https://www.m-x.ca/en/trading/data/quotes?symbol=' + quote(sig.symbol)
    verify_section = (
        f"\n<b>🔎 Recheck delayed quotes and spread:</b>\n"
        f'  • <a href="{mx_url}">Montreal Exchange {_esc(sig.symbol)} native chain</a>\n'
        f'  • <a href="{barchart_url}">Barchart {_esc(sig.symbol)}.TO options</a>\n'
        f'  • <a href="{tmx_url}">TMX Money {_esc(sig.symbol)} options</a>\n'
    )

    # v4.5 — source/currency tag line for the footer
    source = (sig.chain_source or "MX").upper()
    if source == "IBKR":
        src_tag = (f"Source: <b>IBKR Paper</b> — CDE contract quote on TSX "
                   f"<b>{_esc(sig.options_src)}</b>; delta/IV supplied by IBKR.")
    elif source == "MX":
        src_tag = (f"Source: <b>Montreal Exchange (CDE)</b> — CAD-native chain on "
                   f"TSX <b>{_esc(sig.options_src)}</b>.")
    elif source == "FX-CONV":
        fx_note = f" (USD→CAD = {sig.fx_rate:.4f})" if sig.fx_rate else ""
        src_tag = (f"Source: US chain on <b>{_esc(sig.options_src)}</b>, "
                   f"FX-converted to CAD{fx_note}. "
                   f"<i>Actual MX strike ladder may differ.</i>")
    else:  # US-ref
        src_tag = (f"Source: US dual-listed chain on <b>{_esc(sig.options_src)}</b> "
                   f"<b>(USD)</b>. MX chain unavailable; FX fallback failed.")

    previous_close = sig.spot_reference == "previous_completed_close"
    spot_label = (f"Previous completed close: {cs}{sig.spot:.2f}\n"
                  "Strike selection reference only; current moneyness unverified.\n" if previous_close
                  else f"Spot:         {cs}{sig.spot:.2f}\n")
    moneyness = ("moneyness unverified" if previous_close else
                 _moneyness_label(sig.option_type, sig.strike, sig.spot))
    browser_section = ""
    if sig.browser_note:
        browser_section = "\n<b>Browser evidence:</b>\n" + _esc(sig.browser_note) + "\n"
    if sig.browser_ai_review:
        browser_section += "OpenClaw: " + _esc(sig.browser_ai_review.get('summary', '')) + "\n"
        if sig.browser_ai_review.get('concerns'):
            browser_section += "Review concern: " + _esc(sig.browser_ai_review['concerns']) + "\n"

    return (
        f"<b>📊 OPTIONS — {sig.symbol} ({ccy})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Type:         <b>{type_emoji}</b>\n"
        f"Expiry:       <b>{sig.expiry}</b> ({sig.dte}d)\n"
        f"Strike:       <b>{cs}{sig.strike:.2f}</b> ({moneyness})\n"
        + spot_label
        + prem_line
        + "\n"
        + walls
        + pc_note
        + greeks_section
        + warns_section
        + "\n<b>Underlying basis:</b>\n"
        + reasons_txt + "\n"
        + fund_section
        + llm_section
        + browser_section
        + verify_section
        + "\n"
        + regime_line
        + f"Confidence:   {stars} ({sig.confidence}/5)\n"
        + "\n"
        f"<i>ℹ️ Lots: your call — decide in IBKR mobile.</i>\n"
        f"<i>{src_tag} 0 DTE never signaled.</i>"
    )


# ---------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------
if __name__ == "__main__":
    import sys

    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "RY"
    print(f"Options signal test for {sym}...")

    try:
        from ibkr_client import IBKRClient
        client = IBKRClient()
        client.connect()
        bars     = client.get_historical_bars(sym, "60 D", "1 day", "CAD")
        xic_raw  = client.get_historical_bars("XIC", "60 D", "1 day", "CAD")
        client.disconnect()
        df = pd.DataFrame(bars)
        xic_df = pd.DataFrame(xic_raw)
        if not df.empty:
            df.columns = [c.lower() for c in df.columns]
        if not xic_df.empty:
            xic_df.columns = [c.lower() for c in xic_df.columns]
    except Exception as e:
        print(f"IBKR fetch failed: {e}")
        df = pd.DataFrame()
        xic_df = None

    if df.empty:
        print("No bars — cannot score.")
        sys.exit(1)

    result = score_options_signal(sym, df, currency="CAD", xic_bars=xic_df)
    if result is None:
        print(f"No clear directional setup for {sym} (neutral or no options data).")
        sys.exit(0)

    print(f"\n{'='*50}")
    print(f"Signal:   {result.option_type} | Expiry: {result.expiry} ({result.dte}d) | Strike: ${result.strike:.2f}")
    print(f"Spot:     ${result.spot:.2f} | Premium: ${result.est_premium}")
    print(f"Delta:    {result.delta}  Gamma: {result.gamma}  IV: {(result.implied_vol or 0)*100:.1f}%")
    print(f"Beta:     {result.beta}   Alpha: {result.alpha}%/yr")
    print(f"Confidence: {result.confidence}/5")
    print("Reasons:")
    for r in result.reasons:
        print(f"  • {r}")
    if result.warnings:
        print("Warnings:")
        for w in result.warnings:
            print(f"  ⚠ {w}")
    print(f"\n{'='*50}\nTelegram card:\n")
    print(format_options_card(result, regime="🟢 BULLISH", gold_tag="GOLD-FLAT"))
