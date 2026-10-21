"""
get_orders.py
-------------
Prints all currently open/pending orders.

Usage:
    python get_orders.py
"""

import json
from ibkr_client import IBKRClient

if __name__ == "__main__":
    client = IBKRClient()
    client.connect()
    orders = client.get_open_orders()
    client.disconnect()
    print(json.dumps(orders, indent=2, default=str))
