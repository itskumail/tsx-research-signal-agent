"""
eval_signals.py — one-off audit: how did every sent signal actually perform?

Stock signals (logs/signals_sent_*.jsonl):
  For each BUY, walk forward daily bars from the signal date.
  Outcome = TP_FIRST / SL_FIRST / OPEN (neither hit yet), plus 5-day and
  10-day raw returns from entry_price.

Options signals (logs/options_signals_sent_*.jsonl):
  Direction check: CALL is "right" if spot went UP by expiry (or today if
  expiry is in the future); PUT is "right" if spot went DOWN.
  Also reports max favorable move toward strike.

Read-only. Uses yfinance only (no IBKR).
"""
from __future__ import annotations
import json, glob, sys
from datetime import datetime, timezone
from collections import Counter, defaultdict

import pandas as pd
import yfinance as yf
from yf_symbols import yf_ticker_candidates

LOGS = "logs"


def load_jsonl(pattern):
    rows = []
    for f in sorted(glob.glob(f"{LOGS}/{pattern}")):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


_px_cache: dict[str, pd.DataFrame] = {}

def get_bars(sym: str) -> pd.DataFrame | None:
    if sym in _px_cache:
        return _px_cache[sym]
    for cand in yf_ticker_candidates(sym):
        try:
            df = yf.Ticker(cand).history(period="4mo", interval="1d", auto_adjust=False)
            if df is not None and len(df) > 5:
                df.index = df.index.tz_localize(None)
                _px_cache[sym] = df
                return df
        except Exception:
            continue
    _px_cache[sym] = None
    return None


def eval_stock(sig):
    sym = sig["symbol"]
    ts = datetime.fromisoformat(sig["timestamp"]).astimezone(timezone.utc).replace(tzinfo=None)
    entry, tp, sl = sig["entry_price"], sig.get("take_profit"), sig.get("stop_loss")
    df = get_bars(sym)
    if df is None or not entry:
        return None
    fwd = df[df.index > ts.replace(hour=0, minute=0, second=0, microsecond=0)]
    # drop the signal day itself only if signal was sent intraday before close —
    # keep it: TP/SL can hit same day after the signal. Conservative: include.
    fwd = df[df.index >= pd.Timestamp(ts.date())]
    if len(fwd) == 0:
        return None
    outcome, days_to = "OPEN", None
    for i, (dt, row) in enumerate(fwd.iterrows()):
        hit_tp = tp and row["High"] >= tp
        hit_sl = sl and row["Low"] <= sl
        if hit_tp and hit_sl:
            outcome, days_to = "AMBIG", i; break
        if hit_sl:
            outcome, days_to = "SL_FIRST", i; break
        if hit_tp:
            outcome, days_to = "TP_FIRST", i; break
    def ret_n(n):
        if len(fwd) > n:
            return (fwd["Close"].iloc[n] / entry - 1) * 100
        return None
    last_ret = (fwd["Close"].iloc[-1] / entry - 1) * 100
    return dict(symbol=sym, date=str(ts.date()), outcome=outcome, days_to=days_to,
                r5=ret_n(5), r10=ret_n(10), r_now=last_ret)


def get_bars_us(sym: str) -> pd.DataFrame | None:
    key = f"US:{sym}"
    if key in _px_cache:
        return _px_cache[key]
    try:
        df = yf.Ticker(sym).history(period="4mo", interval="1d", auto_adjust=False)
        if df is not None and len(df) > 5:
            df.index = df.index.tz_localize(None)
            _px_cache[key] = df
            return df
    except Exception:
        pass
    _px_cache[key] = None
    return None


def eval_option(sig):
    sym = sig["symbol"]
    ts = datetime.fromisoformat(sig["timestamp"]).astimezone(timezone.utc).replace(tzinfo=None)
    spot, otype = sig.get("spot"), sig.get("option_type")
    expiry = pd.Timestamp(sig["expiry"])
    if not spot:
        return None
    # Spot may be USD (US-listed chain via options_src). Pick the price series
    # whose close at the signal date is nearest to the recorded spot.
    src = sig.get("options_src") or sym
    cands = [get_bars(sym), get_bars_us(src), get_bars_us(sym)]
    df, best_err = None, 0.15  # require within 15% of recorded spot
    for c in cands:
        if c is None:
            continue
        near = c[c.index <= pd.Timestamp(ts.date())]
        if len(near) == 0:
            continue
        err = abs(near["Close"].iloc[-1] / spot - 1)
        if err < best_err:
            df, best_err = c, err
    if df is None:
        return None
    fwd = df[(df.index >= pd.Timestamp(ts.date())) & (df.index <= expiry)]
    if len(fwd) == 0:
        return None
    end_px = fwd["Close"].iloc[-1]
    move = (end_px / spot - 1) * 100
    if otype == "CALL":
        right = move > 0.5
        wrong = move < -0.5
        best = (fwd["High"].max() / spot - 1) * 100
    else:
        right = move < -0.5
        wrong = move > 0.5
        best = (spot / fwd["Low"].min() - 1) * 100  # favorable down-move
    verdict = "RIGHT" if right else ("WRONG" if wrong else "FLAT")
    return dict(symbol=sym, date=str(ts.date()), type=otype, move=move,
                best_fav=best, verdict=verdict, expiry=sig["expiry"])


def main():
    stocks = load_jsonl("signals_sent_*.jsonl")
    opts = load_jsonl("options_signals_sent_*.jsonl")
    print(f"Loaded {len(stocks)} stock signals, {len(opts)} options signals\n")

    # ---- stocks ----
    res = [r for r in (eval_stock(s) for s in stocks if s.get("sendable", True)) if r]
    cnt = Counter(r["outcome"] for r in res)
    n = len(res)
    print("=" * 64)
    print(f"STOCK SIGNALS ({n} evaluable)")
    print("=" * 64)
    for k in ("TP_FIRST", "SL_FIRST", "AMBIG", "OPEN"):
        print(f"  {k:9}: {cnt.get(k,0):3}  ({cnt.get(k,0)/n*100:.0f}%)" if n else "")
    r5 = [r["r5"] for r in res if r["r5"] is not None]
    r10 = [r["r10"] for r in res if r["r10"] is not None]
    rn = [r["r_now"] for r in res if r["r_now"] is not None]
    if r5:
        print(f"\n  Avg +5d return : {sum(r5)/len(r5):+.2f}%   win-rate(+5d>0): {sum(1 for x in r5 if x>0)/len(r5)*100:.0f}%")
    if r10:
        print(f"  Avg +10d return: {sum(r10)/len(r10):+.2f}%   win-rate(+10d>0): {sum(1 for x in r10 if x>0)/len(r10)*100:.0f}%")
    if rn:
        print(f"  Avg to-date    : {sum(rn)/len(rn):+.2f}%")

    # per-symbol breakdown
    by_sym = defaultdict(list)
    for r in res:
        by_sym[r["symbol"]].append(r)
    print("\n  Per-symbol (n>=2):")
    for sym, rows in sorted(by_sym.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < 2:
            continue
        tp = sum(1 for r in rows if r["outcome"] == "TP_FIRST")
        sl = sum(1 for r in rows if r["outcome"] == "SL_FIRST")
        avg5 = [r["r5"] for r in rows if r["r5"] is not None]
        a5 = f"{sum(avg5)/len(avg5):+.1f}%" if avg5 else "  n/a"
        print(f"    {sym:6} n={len(rows):2}  TP-first={tp} SL-first={sl}  avg5d={a5}")

    # worst signals
    worst = sorted((r for r in res if r["r5"] is not None), key=lambda r: r["r5"])[:8]
    print("\n  Worst 8 by 5-day return:")
    for r in worst:
        print(f"    {r['date']} {r['symbol']:6} 5d={r['r5']:+.1f}%  outcome={r['outcome']}")

    # ---- options ----
    ores = [r for r in (eval_option(o) for o in opts) if r]
    print("\n" + "=" * 64)
    print(f"OPTIONS SIGNALS ({len(ores)} evaluable)")
    print("=" * 64)
    oc = Counter(r["verdict"] for r in ores)
    no = len(ores)
    for k in ("RIGHT", "WRONG", "FLAT"):
        print(f"  {k:6}: {oc.get(k,0):3}  ({oc.get(k,0)/no*100:.0f}%)" if no else "")
    for t in ("CALL", "PUT"):
        sub = [r for r in ores if r["type"] == t]
        if sub:
            sc = Counter(r["verdict"] for r in sub)
            avg = sum(r["move"] for r in sub) / len(sub)
            # for PUT a negative move is good
            print(f"  {t}s: n={len(sub)}  RIGHT={sc.get('RIGHT',0)} WRONG={sc.get('WRONG',0)} FLAT={sc.get('FLAT',0)}  avg spot move={avg:+.2f}%")
    print("\n  Worst 8 options calls:")
    keyf = lambda r: r["move"] if r["type"] == "CALL" else -r["move"]
    for r in sorted(ores, key=keyf)[:8]:
        print(f"    {r['date']} {r['symbol']:6} {r['type']:4} spot moved {r['move']:+.1f}% by {r['expiry']}  -> {r['verdict']}")

    # dump full CSVs for inspection
    pd.DataFrame(res).to_csv("logs/eval_stock_signals.csv", index=False)
    pd.DataFrame(ores).to_csv("logs/eval_options_signals.csv", index=False)
    print("\nSaved logs/eval_stock_signals.csv and logs/eval_options_signals.csv")


if __name__ == "__main__":
    main()
