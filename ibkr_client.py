"""
ibkr_client.py
--------------
Core IBKR connection module. Used by all other scripts.
Do not run this directly - import it.

Account: CAD paper trading account
Exchange: TSX (Canadian stocks)
"""

import os
import time
from dotenv import load_dotenv
from ib_insync import IB, Stock, MarketOrder, LimitOrder

load_dotenv()

HOST      = os.getenv("IBKR_HOST",      "127.0.0.1")
PORT      = int(os.getenv("IBKR_PORT",  "7497"))
CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

# Default for this account — Canadian stocks on TSX priced in CAD
DEFAULT_EXCHANGE = "SMART"
DEFAULT_CURRENCY = "CAD"


class IBKRClient:

    def __init__(self, client_id: int | None = None):
        self.ib = IB()
        self.client_id = client_id if client_id is not None else CLIENT_ID

    def connect(self):
        client_ids = [self.client_id]
        # If another local tool is already connected with the default ID,
        # retry a few nearby IDs. This is read/analysis-only in this project,
        # but IBKR still requires every connection to have a unique clientId.
        client_ids.extend(self.client_id + i for i in range(10, 15))

        last_error = None
        for cid in client_ids:
            try:
                self.ib.connect(HOST, PORT, clientId=cid)
                self.client_id = cid
                print(f"Connected to IB Gateway @ {HOST}:{PORT} (clientId {cid})")
                return
            except Exception as e:
                last_error = e
                if "client id is already in use" not in str(e).lower():
                    break

        print(f"ERROR: Could not connect - {last_error}")
        print("Make sure IB Gateway is running and logged in.")
        raise last_error

    def disconnect(self):
        self.ib.disconnect()

    # ── Account ───────────────────────────────────────────────────────────────
    def get_account_summary(self) -> dict:
        raw  = self.ib.accountSummary()
        keys = {"TotalCashValue", "NetLiquidation", "BuyingPower",
                "UnrealizedPnL", "RealizedPnL", "GrossPositionValue"}
        return {
            item.tag: f"{float(item.value):,.2f} {item.currency}"
            for item in raw if item.tag in keys
        }

    # ── Portfolio ─────────────────────────────────────────────────────────────
    def get_portfolio(self) -> list:
        return [
            {
                "symbol":         p.contract.symbol,
                "currency":       p.contract.currency,
                "quantity":       p.position,
                "avg_cost":       round(p.averageCost, 4),
                "market_price":   round(p.marketPrice, 4),
                "market_value":   round(p.marketValue, 2),
                "unrealized_pnl": round(p.unrealizedPNL, 2),
                "realized_pnl":   round(p.realizedPNL, 2),
            }
            for p in self.ib.portfolio()
        ]

    # ── Quotes ────────────────────────────────────────────────────────────────
    def get_quotes(self, symbols: list, currency: str = DEFAULT_CURRENCY) -> list:
        """
        Get delayed quotes for a list of symbols.
        currency: "CAD" for TSX stocks (default), "USD" for US stocks
        """
        # 3 = delayed free data, 1 = live (requires paid subscription)
        self.ib.reqMarketDataType(3)
        contracts = [Stock(s, DEFAULT_EXCHANGE, currency) for s in symbols]
        tickers   = self.ib.reqTickers(*contracts)
        time.sleep(3)
        return [
            {
                "symbol":    t.contract.symbol,
                "currency":  t.contract.currency,
                "last":      t.last,
                "bid":       t.bid,
                "ask":       t.ask,
                "close":     t.close,
                "volume":    t.volume,
                "data_type": "delayed",
            }
            for t in tickers
        ]

    # ── Historical bars ───────────────────────────────────────────────────────
    def get_historical_bars(self, symbol: str,
                             duration: str = "5 D",
                             bar_size: str = "1 hour",
                             currency: str = DEFAULT_CURRENCY) -> list:
        """
        Pull historical OHLCV bars.
        duration : "1 D", "5 D", "1 M", "3 M", "1 Y"
        bar_size : "1 min", "5 mins", "15 mins", "1 hour", "1 day"
        """
        contract = Stock(symbol, DEFAULT_EXCHANGE, currency)
        bars = self.ib.reqHistoricalData(
            contract, endDateTime="",
            durationStr=duration, barSizeSetting=bar_size,
            whatToShow="MIDPOINT", useRTH=True,
        )
        return [{"date": str(b.date), "open": b.open, "high": b.high,
                 "low": b.low, "close": b.close, "volume": b.volume}
                for b in bars]

    # ── Open orders ───────────────────────────────────────────────────────────
    def get_open_orders(self) -> list:
        self.ib.reqOpenOrders()
        self.ib.sleep(1)
        return [
            {
                "symbol": t.contract.symbol,
                "action": t.order.action,
                "qty":    t.order.totalQuantity,
                "type":   t.order.orderType,
                "status": t.orderStatus.status,
            }
            for t in self.ib.trades()
            if t.orderStatus.status not in ("Filled", "Cancelled", "Inactive")
        ]

    # ── Place orders ──────────────────────────────────────────────────────────
    def place_market_order(self, symbol: str, action: str, quantity: int,
                           currency: str = DEFAULT_CURRENCY):
        contract = Stock(symbol, DEFAULT_EXCHANGE, currency)
        trade    = self.ib.placeOrder(contract, MarketOrder(action.upper(), quantity))
        self.ib.sleep(1)
        return {"symbol": symbol, "action": action, "qty": quantity,
                "currency": currency, "type": "MARKET",
                "status": trade.orderStatus.status}

    def place_limit_order(self, symbol: str, action: str,
                          quantity: int, limit_price: float,
                          currency: str = DEFAULT_CURRENCY):
        contract = Stock(symbol, DEFAULT_EXCHANGE, currency)
        trade    = self.ib.placeOrder(contract, LimitOrder(action.upper(), quantity, limit_price))
        self.ib.sleep(1)
        return {"symbol": symbol, "action": action, "qty": quantity,
                "currency": currency, "type": "LIMIT",
                "limit_price": limit_price, "status": trade.orderStatus.status}
