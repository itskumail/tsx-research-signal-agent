"""
get_quotes.py
-------------
Prints delayed quotes for symbols.
Reads watchlist.txt by default, or pass symbols as arguments.
Defaults to CAD (TSX). Add --usd for US stocks.

Usage:
    python get_quotes.py                        # all symbols in watchlist.txt (CAD)
    python get_quotes.py WFG SU ABX             # specific CAD symbols
    python get_quotes.py AAPL TSLA --usd        # US stocks
"""

import json
import sys
from ibkr_client import IBKRClient

def load_watchlist(path="watchlist.txt") -> list:
    symbols = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    symbols.append(line.upper())
    except FileNotFoundError:
        print("watchlist.txt not found — please create it")
        sys.exit(1)
    return symbols

if __name__ == "__main__":
    args     = sys.argv[1:]
    currency = "USD" if "--usd" in args else "CAD"
    args     = [a for a in args if a != "--usd"]
    symbols  = [s.upper() for s in args] if args else load_watchlist()

    client = IBKRClient()
    client.connect()
    quotes = client.get_quotes(symbols, currency=currency)
    client.disconnect()

    print(json.dumps(quotes, indent=2, default=str))
