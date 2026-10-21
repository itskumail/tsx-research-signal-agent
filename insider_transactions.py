"""
insider_transactions.py
-----------------------
Recent insider buying/selling for a symbol. Two free sources:

  1. yfinance Ticker.insider_transactions (and .insider_purchases) — primary
  2. Barchart /insider-trades page — fallback / supplement (already pulled
     by enrichment.enrich_barchart() and exposed as barchart_insider_net)

Returns a dict summarizing last 30/90 day net activity. If insider buying
is meaningful (CEO/CFO buying, multi-million $ purchases), this is a known
bullish edge — they have non-public information about the company.

Used as a BONUS check in signals.score_symbol():
  +1 score   if net insider BUYING in last 30 days (≥ $250k OR officer-level)
  no penalty for net selling — selling is often non-signal (tax, diversification)
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime, timezone, timedelta


# Officer/director title keywords — heavier weight on these
KEY_INSIDER_TITLES = [
    "ceo", "chief executive", "cfo", "chief financial",
    "coo", "chief operating", "president",
    "chairman", "chairwoman", "founder",
    "director",  # board director still counts but lower
]


# Reuse dual-listing map from ownership_data
_US_DUAL = {
    "RY": "RY", "TD": "TD", "BMO": "BMO", "BNS": "BNS", "CM": "CM", "NA": "NA",
    "SU": "SU", "CNQ": "CNQ", "ENB": "ENB", "TRP": "TRP", "CVE": "CVE",
    "MFC": "MFC", "SLF": "SLF", "SHOP": "SHOP", "NTR": "NTR", "AEM": "AEM",
    "ABX": "GOLD", "CCO": "CCJ", "TECK.B": "TECK", "WCN": "WCN", "GIB.A": "GIB",
    "CNR": "CNI", "CP": "CP", "BCE": "BCE", "T": "TU",
}


def get_insider_summary(symbol: str, currency: str = "CAD",
                        days_back: int = 30) -> dict:
    """
    Returns {net_buy_count, net_sell_count, key_buy_count,
             total_buy_value, total_sell_value, src} or {}.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    # Only the resolved Canadian listing plus the curated same-issuer US
    # listing. Empty Canadian insider data is common, so the generic suffix
    # and bare-symbol fallbacks would reach other companies' insiders
    # (T = AT&T, CCO = Clear Channel); missing data stays missing.
    from yf_symbols import to_yf_ticker
    candidates = [to_yf_ticker(symbol, currency=currency)]
    if currency.upper() == "CAD" and symbol in _US_DUAL:
        if _US_DUAL[symbol] not in candidates:
            candidates.append(_US_DUAL[symbol])

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days_back)

    for cand in candidates:
        try:
            t = yf.Ticker(cand)
            it = t.insider_transactions
        except Exception:
            continue
        if it is None or len(it) == 0:
            continue

        try:
            buys = sells = key_buys = 0
            buy_val = sell_val = 0.0

            for _, row in it.iterrows():
                # row schema varies — try multiple keys
                date_val = (row.get("Start Date") or row.get("Date")
                            or row.get("startDate") or row.get("date"))
                try:
                    dt = (date_val.date() if hasattr(date_val, "date")
                          else datetime.fromisoformat(str(date_val)[:10]).date())
                except Exception:
                    continue
                if dt < cutoff:
                    continue

                action = str(row.get("Transaction") or row.get("transactionText")
                             or row.get("Text") or "").lower()
                shares = row.get("Shares") or row.get("shares") or 0
                value  = row.get("Value")  or row.get("value")  or 0.0
                title  = str(row.get("Position") or row.get("title") or "").lower()

                try: shares = float(shares) if shares else 0.0
                except Exception: shares = 0.0
                try: value  = float(value)  if value  else 0.0
                except Exception: value = 0.0

                is_key = any(t in title for t in KEY_INSIDER_TITLES)

                if "buy" in action or "purchase" in action or "acqui" in action:
                    buys += 1
                    buy_val += abs(value)
                    if is_key:
                        key_buys += 1
                elif "sell" in action or "dispose" in action or "sale" in action:
                    sells += 1
                    sell_val += abs(value)

            if buys + sells > 0:
                return {
                    "symbol": symbol,
                    "src": cand,
                    "days_back": days_back,
                    "buy_count": buys,
                    "sell_count": sells,
                    "key_officer_buys": key_buys,
                    "buy_value_usd": buy_val,
                    "sell_value_usd": sell_val,
                    "net_value_usd": buy_val - sell_val,
                }
        except Exception:
            continue

    return {}


def insider_signal_modifier(insider: dict) -> tuple[int, str | None]:
    """
    Returns (score_delta, note).
    +1 if last-30d insider buying is meaningful:
        - any officer (CEO/CFO/President/etc.) buying, OR
        - net insider $ value > $250k
    No penalty for selling (often non-signal).
    """
    if not insider:
        return (0, None)
    key_buys = insider.get("key_officer_buys", 0)
    net_val  = insider.get("net_value_usd", 0.0)
    bb       = insider.get("buy_count", 0)
    if key_buys > 0:
        return (1, f"Officer-level insider buying ({key_buys} key insider, {bb} buy txns)")
    if net_val > 250_000:
        return (1, f"Insider net buying ${net_val:,.0f} last 30d ({bb} txns)")
    return (0, None)


def format_insider_lines(insider: dict) -> list[str]:
    """Telegram card lines for insider activity. Empty if nothing notable."""
    if not insider:
        return []
    bb = insider.get("buy_count", 0)
    ss = insider.get("sell_count", 0)
    nv = insider.get("net_value_usd", 0.0)
    kb = insider.get("key_officer_buys", 0)
    if bb + ss == 0:
        return []
    lines = []
    if nv > 0:
        emoji = "🟢"
    elif nv < 0:
        emoji = "🔴"
    else:
        emoji = "⚪"
    lines.append(f"  • {emoji} Insider 30d: {bb} buy / {ss} sell · net ${nv:+,.0f}")
    if kb > 0:
        lines.append(f"    └ <b>{kb} officer-level buy(s)</b>")
    return lines


if __name__ == "__main__":
    import sys, json
    sym = sys.argv[1] if len(sys.argv) > 1 else "RY"
    print(json.dumps(get_insider_summary(sym), indent=2, default=str))
