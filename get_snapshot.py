"""
get_snapshot.py
---------------
Prints a full account snapshot: account summary, portfolio,
watchlist quotes, and open orders.

Claude Code runs this to read the current state of your account.

Usage:
    python get_snapshot.py
"""

import json
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
        symbols = ["AAPL", "TSLA", "SPY"]
    return symbols

if __name__ == "__main__":
    client = IBKRClient()
    client.connect()

    watchlist = load_watchlist()

    snapshot = {
        "account":     client.get_account_summary(),
        "portfolio":   client.get_portfolio(),
        "quotes":      client.get_quotes(watchlist),
        "open_orders": client.get_open_orders(),
        "watchlist":   watchlist,
    }

    client.disconnect()
    print(json.dumps(snapshot, indent=2, default=str))
