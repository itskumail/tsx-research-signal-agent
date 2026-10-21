"""
deep_scan.py — v5.6 full-watchlist DeepSeek triage
---------------------------------------------------
The user's requirement: EVERY watchlist name — small or large — gets a real
look each session (momentum, cash flow, earnings quality, options put/call
positioning, news), and DeepSeek decides direction. No big-cap technical
pre-filter deciding who the AI is allowed to see.

Pipeline position: runs inside the session scoring loop (which already
fetches two years of bars for every name, so momentum costs zero extra network).

  build_factsheet(sym, sig, df, news_events)
      → compact per-name dict from already-computed indicators
        + daily-cached fundamentals + daily-cached put/call OI sentiment

  run_triage(factsheets, market_ctx, session_name)
      → batches the factsheets to DeepSeek (llm_researcher.triage_watchlist),
        returns {SYMBOL: {"direction": LONG|SHORT|NONE,
                          "conviction": 1-5, "reason": str}}
        Saves the verdicts to logs/deep_scan_<date>_<session>.json for audit.

Daily caches (JSON in logs/):
  fundamentals_scan_<date>.json — fundamentals.fetch_fundamentals per name
  pc_sentiment_<date>.json      — options_analyzer.analyze_options pc_ratio
                                  for DUAL_LIST_MAP names

All functions fail soft: any error degrades to "field absent", never raises
into the session.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta

LOGS_DIR = "logs"

# Triage injection thresholds (consumed by session_runner)
TRIAGE_MIN_CONVICTION = 4      # LONG verdicts at/above this join the research pool
TRIAGE_BATCH_SIZE = 10         # smaller final JSON responses reduce truncation
TRIAGE_TIMEOUT = 360           # total first pass plus one missing-only repair pass


# ---------------------------------------------------------------
# Daily caches
# ---------------------------------------------------------------
def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _cache_path(prefix: str) -> str:
    return os.path.join(LOGS_DIR, f"{prefix}_{_today()}.json")


def _load_cache(prefix: str) -> dict:
    try:
        with open(_cache_path(prefix), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(prefix: str, data: dict) -> None:
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(_cache_path(prefix), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


# ---------------------------------------------------------------
# Fundamentals (cached once per day per symbol)
# ---------------------------------------------------------------
_fund_cache: dict | None = None

def fundamentals_lite(symbol: str) -> dict | None:
    """FCF / leverage / quality snapshot, cached daily. None when unavailable."""
    global _fund_cache
    if _fund_cache is None:
        _fund_cache = _load_cache("fundamentals_scan")
    key = symbol.upper()
    if key in _fund_cache:
        return _fund_cache[key] or None
    result = None
    try:
        import fundamentals
        f = fundamentals.fetch_fundamentals(symbol, exchange_suffix=".TO")
        if f and f.get("available"):
            # Keep only the compact fields the triage prompt needs
            result = {
                "quality":            f.get("quality_tag"),
                "fcf_ttm":            f.get("fcf_ttm"),
                "fcf_yoy_pct":        f.get("fcf_yoy_pct"),
                "fcf_yield_pct":      f.get("fcf_yield_pct"),
                "net_debt_to_ebitda": f.get("net_debt_to_ebitda"),
            }
    except Exception:
        result = None
    _fund_cache[key] = result
    _save_cache("fundamentals_scan", _fund_cache)
    return result


# ---------------------------------------------------------------
# Put/Call OI sentiment (cached once per day per symbol)
# ---------------------------------------------------------------
_pc_cache: dict | None = None

def pc_sentiment(symbol: str) -> dict | None:
    """
    Options-market positioning for dual-listed names: put/call OI ratio,
    max pain, walls. None for names without a usable chain.
    """
    global _pc_cache
    if _pc_cache is None:
        _pc_cache = _load_cache("pc_sentiment")
    key = symbol.upper()
    if key in _pc_cache:
        cached = _pc_cache[key]
        if not cached:
            return None
        return {**cached, "tilt": "OI context only; direction unknown", "currency": "USD", "source": "US dual-listed chain"}
    result = None
    try:
        from options_analyzer import analyze_options, DUAL_LIST_MAP
        if symbol in DUAL_LIST_MAP:
            opt = analyze_options(symbol, currency="CAD")
            if opt and opt.get("pc_ratio") is not None:
                pc = opt["pc_ratio"]
                tilt = "OI context only; direction unknown"
                result = {
                    "pc_ratio": round(pc, 2),
                    "tilt":     tilt,
                    "max_pain": opt.get("max_pain"),
                    "expiry":   opt.get("expiry"),
                    "currency": "USD", "source": "US dual-listed chain",
                }
    except Exception:
        result = None
    _pc_cache[key] = result
    _save_cache("pc_sentiment", _pc_cache)
    return result


# ---------------------------------------------------------------
# Factsheet — one compact dict per watchlist name
# ---------------------------------------------------------------
def _pct(a: float, b: float) -> float | None:
    try:
        if b and b > 0:
            return round((a / b - 1) * 100, 1)
    except Exception:
        pass
    return None


def build_factsheet(symbol: str, sig, df, news_events: list | None) -> dict:
    """
    sig — Signal from signals.score_symbol (indicators already computed)
    df  — the completed daily bars the scorer used
    """
    ind = getattr(sig, "indicators", {}) or {}
    close = ind.get("close")
    fs: dict = {
        "symbol": symbol.upper(),
        "close": close,
        "trend": ind.get("trend"),
        "rsi": round(ind.get("rsi_14") or 0, 1) or None,
        "vol_ratio": round(ind.get("vol_ratio") or 0, 2) or None,
        "passed_rubric": bool(getattr(sig, "sendable", False)),
    }
    # Momentum from the bars we already have (no extra network)
    try:
        closes = df["close"].astype(float)
        n = len(closes)
        if close:
            if n > 5:
                fs["ret_1w_pct"] = _pct(close, float(closes.iloc[-6]))
            if n > 21:
                fs["ret_1m_pct"] = _pct(close, float(closes.iloc[-22]))
            if n > 63:
                fs['ret_3m_pct'] = _pct(close, float(closes.iloc[-64]))
            fs["dist_60d_high_pct"] = _pct(close, float(df["high"].astype(float).tail(60).max()))
    except Exception:
        pass
    # Catalyst (already detected by session_runner on every name)
    cat = ind.get("_catalyst")
    if cat:
        fs["catalyst"] = "; ".join(cat.get("reasons", []))[:160]
    # News direction (strong matches only)
    if news_events:
        try:
            import filters
            nd, headline = filters.news_sentiment(news_events, symbol)
            if nd:
                fs["news"] = f"{'bullish' if nd > 0 else 'bearish'}: {str(headline)[:120]}"
        except Exception:
            pass
    # Fundamentals + options positioning (daily-cached)
    f = fundamentals_lite(symbol)
    if f:
        fs["fundamentals"] = f
    pc = pc_sentiment(symbol)
    if pc:
        fs["options_positioning"] = pc
    return fs


# ---------------------------------------------------------------
# Triage — DeepSeek over the whole watchlist
# ---------------------------------------------------------------
def run_triage(factsheets: list[dict], market_ctx: dict,
               session_name: str) -> dict:
    """
    Returns {SYMBOL: {"direction": ..., "conviction": int, "reason": ...}}.
    Empty dict when the LLM is disabled or everything fails.
    """
    try:
        import llm_researcher
        if not llm_researcher.is_enabled() or not factsheets:
            return {}
    except Exception:
        return {}

    verdicts: dict = {}
    deadline = time.monotonic() + TRIAGE_TIMEOUT
    # Repair only absent/invalid verdicts once; keep valid first-pass decisions.
    pending = list({str(fs.get('symbol') or '').upper(): fs for fs in factsheets}.values())
    attempts = []
    for pass_number, size in ((1, TRIAGE_BATCH_SIZE), (2, 5)):
        batches = [pending[i:i + size] for i in range(0, len(pending), size)]
        for bi, batch in enumerate(batches, 1):
            remaining = deadline - time.monotonic()
            if remaining < 15:
                break
            batch_symbols = {str(fs.get('symbol') or '').upper() for fs in batch}
            try:
                print(f"[deep-scan] Triage pass {pass_number} batch {bi}/{len(batches)} "
                      f"({len(batch)} names)...")
                repair_reserve = 60 if pass_number == 1 else 0
                timeout = min(60, max(1, remaining - repair_reserve) / max(1, len(batches) - bi + 1))
                out = llm_researcher.triage_watchlist(batch, market_ctx, timeout=timeout)
                for v in out:
                    if not isinstance(v, dict):
                        continue
                    sym = str(v.get('symbol') or '').upper()
                    if not sym or sym not in batch_symbols or sym in verdicts:
                        continue
                    direction = str(v.get('direction') or '').upper()
                    if direction not in ('LONG', 'SHORT', 'NONE'):
                        continue
                    conviction = v.get('conviction')
                    if type(conviction) is not int or not 1 <= conviction <= 5:
                        continue
                    verdicts[sym] = {
                        'direction': direction, 'conviction': conviction,
                        'reason': str(v.get('reason') or '')[:200],
                    }
            except Exception:
                print(f"[deep-scan] batch {bi} unavailable; missing verdicts remain unknown.")
            attempts.append({'pass': pass_number, 'requested': sorted(batch_symbols),
                             'completed': sorted(set(verdicts).intersection(batch_symbols))})
        pending = [fs for fs in factsheets if str(fs.get('symbol') or '').upper() not in verdicts]
        if not pending or deadline - time.monotonic() < 15:
            break

    expected_symbols = {str(fs.get('symbol') or '').upper() for fs in factsheets}
    missing_symbols = sorted(expected_symbols - set(verdicts))
    if missing_symbols:
        print(f'[deep-scan] Coverage incomplete: {len(verdicts)}/{len(expected_symbols)} verdicts. '
              'Missing names do not have an AI direction.')
    # Audit trail
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        path = os.path.join(
            LOGS_DIR, f"deep_scan_{_today()}_{session_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "ts": datetime.now(timezone.utc).isoformat(),
                "session": session_name,
                "n_factsheets": len(factsheets),
                "missing_symbols": missing_symbols,
                "coverage_complete": not missing_symbols,
                "attempts": attempts,
                "verdicts": verdicts,
            }, f, indent=1)
    except Exception:
        pass
    return verdicts


def long_picks(verdicts: dict, min_conviction: int = TRIAGE_MIN_CONVICTION) -> list[str]:
    return [s for s, v in verdicts.items()
            if v["direction"] == "LONG" and v["conviction"] >= min_conviction]


def short_picks(verdicts: dict, min_conviction: int = TRIAGE_MIN_CONVICTION) -> list[str]:
    return [s for s, v in verdicts.items()
            if v["direction"] == "SHORT" and v["conviction"] >= min_conviction]
