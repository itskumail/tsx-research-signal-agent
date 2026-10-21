"""
options_analyzer.py
-------------------
Options-chain context for signal candidates. yfinance-based, no scraping.

For each candidate symbol, finds the nearest monthly expiry (5-60 days out),
pulls the full options chain, and computes:

  • Max pain strike  — where market expects price at expiry (institutional pin)
  • Put wall         — highest-OI put strike near spot = institutional SUPPORT
  • Call wall        — highest-OI call strike just above spot = ceiling / resistance
  • Pin candidate    — close within 2% of max pain AND <5 days to expiry

Returns {} if:
  • Symbol has no listed options (most TSX mid-caps)
  • Total OI is below 100 (data too thin to trust)
  • Nearest expiry is < 5 days or > 60 days (signal noisy outside this window)

Score modifiers wired into signals.score_symbol():
  + Near put wall (within 2% above)            → +1 score (bonus check)
  + At max pain with <5d to exp                → +1 score (gravitation)
  − Within 2% under call wall AND <10d to exp  → +1 to min_score (penalty)

Failures are non-fatal — the analyzer always returns {} on any error so the
caller can degrade gracefully.
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

from datetime import date, datetime


# Canadian → US dual-listing map. yfinance has options for the US-listed
# dual but not the .TO symbol; the US chain tracks the TSX shares closely
# enough for max-pain / put-wall / call-wall purposes.
DUAL_LIST_MAP = {
    # Banks (same ticker on both)
    "RY": "RY", "TD": "TD", "BMO": "BMO", "BNS": "BNS", "CM": "CM", "NA": "NA",
    # Energy (same ticker)
    "SU": "SU", "CNQ": "CNQ", "ENB": "ENB", "TRP": "TRP", "CVE": "CVE",
    # Insurance (same ticker)
    "MFC": "MFC", "SLF": "SLF",
    # Tech / e-commerce
    "SHOP": "SHOP",
    # Materials / mining (same ticker except where noted)
    "NTR": "NTR", "AEM": "AEM",
    "ABX": "GOLD",       # Barrick uses GOLD on NYSE
    "CCO": "CCJ",        # Cameco uses CCJ on NYSE
    "TECK.B": "TECK",    # Teck Resources B
    # Industrials / waste
    "WCN": "WCN",
    "GIB.A": "GIB",
    # Misc large dual-listings
    "CNR": "CNI", "CP": "CP", "BCE": "BCE", "T": "TU",
}


def _try_ticker_variants(symbol: str, currency: str) -> list[str]:
    """First try .TO (sometimes works for some names), then dual-listed US."""
    suffix = ".TO" if currency.upper() == "CAD" else ""
    candidates = [f"{symbol}{suffix}"] if suffix else [symbol]
    if currency.upper() == "CAD" and symbol in DUAL_LIST_MAP:
        us = DUAL_LIST_MAP[symbol]
        if us not in candidates:
            candidates.append(us)
    return candidates


# ---------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------
def analyze_options(symbol: str, currency: str = "CAD") -> dict:
    """
    Returns a dict with options analysis or {} if no usable data.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    expiries: list = []
    t = None
    used_ticker = None
    for cand in _try_ticker_variants(symbol, currency):
        try:
            t_try = yf.Ticker(cand)
            exp_try = t_try.options or []
            if exp_try:
                t = t_try
                expiries = list(exp_try)
                used_ticker = cand
                break
        except Exception:
            continue

    if not expiries or t is None:
        return {}

    # Pick nearest expiry within 5-60 day window
    today = date.today()
    chosen_str = None
    chosen_days = None
    for exp_str in expiries:
        try:
            exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
            days = (exp - today).days
            if 5 <= days <= 60:
                chosen_str = exp_str
                chosen_days = days
                break
        except Exception:
            continue

    if chosen_str is None:
        return {}

    try:
        chain = t.option_chain(chosen_str)
        calls = chain.calls
        puts  = chain.puts
    except Exception:
        return {}

    if calls is None or puts is None or len(calls) == 0 or len(puts) == 0:
        return {}

    # Spot price
    try:
        hist = t.history(period="2d")
        if hist is None or len(hist) == 0:
            return {}
        spot = float(hist["Close"].iloc[-1])
    except Exception:
        return {}

    # Total OI sanity check
    try:
        total_call_oi = float(calls["openInterest"].fillna(0).sum())
        total_put_oi  = float(puts["openInterest"].fillna(0).sum())
    except Exception:
        return {}

    total_oi = total_call_oi + total_put_oi
    if total_oi < 100:
        return {}

    # Max pain — strike where total $ in-the-money pain is minimized
    # (writers' best outcome; price tends to gravitate here at expiry)
    all_strikes = sorted(set(calls["strike"].tolist() + puts["strike"].tolist()))
    pain_per_strike = {}
    for strike in all_strikes:
        call_pain = float(((calls["strike"] - strike).clip(upper=0).abs() *
                           calls["openInterest"].fillna(0)).sum())
        put_pain  = float(((strike - puts["strike"]).clip(upper=0).abs() *
                           puts["openInterest"].fillna(0)).sum())
        # Wait — clip(upper=0).abs() is wrong. Let me redo cleanly:
        # call ITM if spot > strike → loss = max(spot - strike, 0)
        # at hypothetical pin price = strike, calls ITM are those with strike < this
        # so call_loss_at(pin) = sum_over_calls(max(pin - call_strike, 0) * OI)
        call_pain = float(((strike - calls["strike"]).clip(lower=0) *
                           calls["openInterest"].fillna(0)).sum())
        put_pain  = float(((puts["strike"] - strike).clip(lower=0) *
                           puts["openInterest"].fillna(0)).sum())
        pain_per_strike[strike] = call_pain + put_pain

    max_pain_strike = min(pain_per_strike, key=pain_per_strike.get)

    # Put wall — highest OI put strike within 10% below spot (real support)
    put_wall = None
    nearby_puts = puts[(puts["strike"] <= spot) & (puts["strike"] >= spot * 0.90)]
    if len(nearby_puts):
        idx = nearby_puts["openInterest"].fillna(0).idxmax()
        if not isinstance(idx, type(None)):
            put_wall = float(nearby_puts.loc[idx, "strike"])

    # Call wall — highest OI call strike within 10% above spot (real ceiling)
    call_wall = None
    nearby_calls = calls[(calls["strike"] >= spot) & (calls["strike"] <= spot * 1.10)]
    if len(nearby_calls):
        idx = nearby_calls["openInterest"].fillna(0).idxmax()
        if not isinstance(idx, type(None)):
            call_wall = float(nearby_calls.loc[idx, "strike"])

    pc_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else None

    return {
        "symbol":       symbol,
        "options_src":  used_ticker,   # which yfinance ticker the chain came from
        "expiry":       chosen_str,
        "days_to_exp":  chosen_days,
        "spot":         spot,
        "max_pain":     float(max_pain_strike),
        "put_wall":     put_wall,
        "call_wall":    call_wall,
        "pc_ratio":     pc_ratio,
        "total_oi":     int(total_oi),
    }


# ---------------------------------------------------------------
# Score modifiers
# ---------------------------------------------------------------
def options_signal_modifiers(opt: dict, close: float) -> tuple[list[str], list[str]]:
    """
    Returns (bonus_notes, penalty_notes):
      bonus_notes   — each adds one score (helps the candidate pass min_score)
      penalty_notes — each adds one to required min_score (effectively a -1 nudge)

    Both lists may be empty when conditions don't trigger.
    """
    if not opt:
        return ([], [])
    bonus_notes:   list[str] = []
    penalty_notes: list[str] = []

    days = opt.get("days_to_exp", 999)

    # 1. Near put wall above (institutional support)
    pw = opt.get("put_wall")
    if pw and close >= pw:
        dist = (close - pw) / pw
        if 0 <= dist <= 0.02:
            bonus_notes.append(f"Near put wall ${pw:.2f} ({dist*100:.1f}% above) - institutional support")

    # 2. At max pain near expiry (pin gravitation)
    mp = opt.get("max_pain")
    if mp and mp > 0 and days <= 5:
        if abs(close - mp) / mp <= 0.02:
            bonus_notes.append(f"At max pain ${mp:.2f} ({days}d to exp) - pin gravitation")

    # 3. Under call wall near expiry (capped upside)
    cw = opt.get("call_wall")
    if cw and close <= cw and days <= 10:
        upside = (cw - close) / cw
        if 0 <= upside <= 0.02:
            penalty_notes.append(f"Just under call wall ${cw:.2f} ({days}d to exp) - capped upside")

    return (bonus_notes, penalty_notes)


# ---------------------------------------------------------------
# Card-footer formatter
# ---------------------------------------------------------------
def format_options_footer(opt: dict) -> str:
    """Compact options block for the Telegram card. Empty if no useful data."""
    if not opt:
        return ""
    src_note = ""
    src = opt.get("options_src", "")
    if src and not src.endswith(".TO"):
        src_note = f"  <i>(via US dual {src})</i>\n"
    lines = [src_note + f"  • Expiry: <b>{opt['expiry']}</b> ({opt['days_to_exp']}d)"]
    if opt.get("max_pain"):
        lines.append(f"  • Max pain: <b>${opt['max_pain']:.2f}</b>")
    if opt.get("put_wall"):
        lines.append(f"  • Put OI concentration (USD): <b>${opt['put_wall']:.2f}</b>")
    if opt.get("call_wall"):
        lines.append(f"  • Call OI concentration (USD): <b>${opt['call_wall']:.2f}</b>")
    if opt.get("pc_ratio") is not None:
        pc = opt["pc_ratio"]
        lines.append(f"  • P/C OI ratio: <b>{pc:.2f}</b> (not direction)")
    lines.append(f"  • Total OI: {opt['total_oi']:,}")
    return "\n<b>📈 Options:</b>\n" + "\n".join(lines)


# ---------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------
if __name__ == "__main__":
    import sys, json
    sym = sys.argv[1] if len(sys.argv) > 1 else "RY"
    print(f"Analyzing options for {sym}.TO ...")
    data = analyze_options(sym)
    if not data:
        print("No usable options data (no chain, thin OI, or expiry outside 5-60d).")
        sys.exit(0)
    print(json.dumps(data, indent=2, default=str))
    bonus, penalty = options_signal_modifiers(data, data["spot"])
    print()
    print("Score modifiers:")
    for n in bonus:   print(f"  +1 score:    {n}")
    for n in penalty: print(f"  +1 penalty:  {n}")
    print()
    print("Card footer:")
    print(format_options_footer(data))
