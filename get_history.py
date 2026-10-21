"""
get_history.py
--------------
Prints historical OHLCV bars for a symbol.
Defaults to CAD (TSX). Add --usd for US stocks.

Usage:
    python get_history.py WFG
    python get_history.py SU "10 D" "1 hour"
    python get_history.py CNQ "1 M" "1 day"
    python get_history.py AAPL "5 D" "1 hour" --usd

Duration options : "1 D", "5 D", "1 M", "3 M", "1 Y"
Bar size options : "1 min", "5 mins", "15 mins", "1 hour", "1 day"
"""

import json
import sys
from ibkr_client import IBKRClient

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0].startswith("-"):
        print("Usage: python get_history.py SYMBOL [duration] [bar_size] [--usd]")
        sys.exit(1)

    currency = "USD" if "--usd" in args else "CAD"
    args     = [a for a in args if a != "--usd"]

    symbol   = args[0].upper()
    duration = args[1] if len(args) > 1 else "5 D"
    bar_size = args[2] if len(args) > 2 else "1 hour"

    client = IBKRClient()
    client.connect()
    bars = client.get_historical_bars(symbol, duration, bar_size, currency)
    client.disconnect()

    print(json.dumps(bars, indent=2, default=str))
