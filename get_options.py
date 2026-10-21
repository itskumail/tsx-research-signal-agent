"""
get_options.py
--------------
Lists available option expiries for a symbol via IBKR.
Reports: nearest expiries, days to expiry, whether weekly or monthly contracts exist.
For deep OI / Put-Call-Ratio analysis, Claude should WebFetch Barchart options page
(link included in the output).

Usage:
    python get_options.py CSU              # Canadian stock (CAD)
    python get_options.py AAPL --usd       # US stock
"""

import json
import sys
from datetime import datetime, date
from ibkr_client import IBKRClient
from ib_insync import Stock

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python get_options.py SYMBOL [--usd]")
        sys.exit(1)

    currency = "USD" if "--usd" in args else "CAD"
    args     = [a for a in args if a != "--usd"]
    symbol   = args[0].upper()

    client = IBKRClient()
    client.connect()
    ib = client.ib

    stock = Stock(symbol, "SMART", currency)
    try:
        ib.qualifyContracts(stock)
    except Exception as e:
        print(json.dumps({"symbol": symbol, "error": str(e)}))
        client.disconnect()
        sys.exit(1)

    chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)

    if not chains:
        result = {
            "symbol": symbol,
            "options_available": False,
            "note": "No listed options for this symbol — skip options analysis",
        }
    else:
        chain     = chains[0]
        today_str = date.today().strftime("%Y%m%d")
        upcoming  = sorted(e for e in chain.expirations if e >= today_str)

        expiry_info = []
        for exp in upcoming[:8]:
            exp_date = datetime.strptime(exp, "%Y%m%d").date()
            dte      = (exp_date - date.today()).days
            weekday  = exp_date.strftime("%A")
            # Monthly expiry = 3rd Friday of the month (day 15-21)
            day  = exp_date.day
            kind = "monthly" if 15 <= day <= 21 and weekday == "Friday" else "weekly"
            expiry_info.append({
                "date":           exp_date.isoformat(),
                "days_to_expiry": dte,
                "weekday":        weekday,
                "type":           kind,
            })

        has_weeklies = any(e["type"] == "weekly" for e in expiry_info)
        exchange_suffix = ".TO" if currency == "CAD" else ""
        barchart_url = f"https://www.barchart.com/stocks/quotes/{symbol}{exchange_suffix}/options"

        result = {
            "symbol":             symbol,
            "currency":           currency,
            "options_available":  True,
            "has_weekly_options": has_weeklies,
            "exchange":           chain.exchange,
            "trading_class":      chain.tradingClass,
            "total_strikes":      len(chain.strikes),
            "upcoming_expiries":  expiry_info,
            "nearest_expiry":     expiry_info[0] if expiry_info else None,
            "research_url":       barchart_url,
            "note":               "For Put/Call ratio, unusual activity, and OI concentration, WebFetch the research_url above.",
        }

    client.disconnect()
    print(json.dumps(result, indent=2, default=str))
