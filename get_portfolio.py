"""
get_portfolio.py
----------------
Prints current open positions and account summary.

Usage:
    python get_portfolio.py
"""

import json
from ibkr_client import IBKRClient

if __name__ == "__main__":
    client = IBKRClient()
    client.connect()

    result = {
        "account":   client.get_account_summary(),
        "portfolio": client.get_portfolio(),
    }

    client.disconnect()
    print(json.dumps(result, indent=2, default=str))
