"""
test_portfolio_review.py
-------------------------
Diagnostic script to test portfolio reviews and Telegram alerts.
This script simulates open positions (RY and SU) to verify that
recommendations (HOLD / MONITOR / CAUTION / EXIT WATCH) are generated
and Telegram notifications are sent correctly.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd

# Ensure C:\Trading is in Python path
TRADING_DIR = Path(r"C:\Trading")
sys.path.append(str(TRADING_DIR))

from ibkr_client import IBKRClient
from session_runner import _analyze_open_position, determine_regime
import filters
import telegram_notify

class MockIBKRClient:
    """Mock IBKR client that falls back to Yahoo Finance data when Gateway is offline."""
    def get_historical_bars(self, symbol: str, duration: str, bar_size: str, currency: str = "CAD") -> list[dict]:
        from session_runner import _yahoo_history
        print(f"      [mock] Fetching historical bars for {symbol} via Yahoo Finance fallback...")
        df = _yahoo_history(symbol, currency=currency, period_days=365)
        if df.empty:
            return []
        return df.to_dict("records")

def run_test():
    print("=== Portfolio Alert Verification Diagnostic ===")
    
    # 1. Setup mock positions
    # - RY (Royal Bank of Canada): Mock avg_cost of $130, market_price $142 (Up 9.2% -> TAKE PROFIT WATCH)
    # - SU (Suncor Energy): Mock avg_cost of $50, market_price $45 (Down 10% -> CAUTION / EXIT WATCH)
    mock_positions = [
        {
            "symbol": "RY",
            "quantity": 50,
            "avg_cost": 130.0,
            "market_price": 142.0,
        },
        {
            "symbol": "SU",
            "quantity": 100,
            "avg_cost": 50.0,
            "market_price": 45.0,
        }
    ]
    
    # 2. Get market context
    print("[+] Determining market regime...")
    regime, gold_tag, market = determine_regime()
    print(f"    Regime: {regime} | Gold: {gold_tag}")
    
    # 3. Connect to Gateway or fall back to Mock
    client = IBKRClient()
    use_mock = False
    try:
        client.connect()
        print("[+] Connected to IB Gateway.")
    except Exception:
        print("[!] Gateway offline. Initializing Mock client with Yahoo Finance fallback.")
        client = MockIBKRClient()
        use_mock = True

    # 4. Analyze each mock position
    print("[+] Running portfolio position analysis...")
    news_events = filters.load_news_events_today()
    reviews = []
    for pos in mock_positions:
        try:
            review = _analyze_open_position(client, pos, market, news_events)
            reviews.append(review)
            pnl_pct = review.get("pnl_pct", 0.0)
            pnl_str = f"{pnl_pct*100:+.1f}%" if pnl_pct is not None else "N/A"
            print(f"    - {pos['symbol']}: {review['recommendation']} ({pnl_str}) -> Reasons: {review['reasons']}")
        except Exception as e:
            print(f"    [ERROR] Failed to analyze {pos['symbol']}: {e}")
            import traceback
            traceback.print_exc()

    # Disconnect if real client was used
    if not use_mock and client is not None:
        try:
            client.disconnect()
        except Exception:
            pass

    # 5. Format and dispatch Telegram notification
    if reviews:
        lines = []
        for r in reviews:
            pnl = r.get("pnl_pct")
            pnl_s = "n/a" if pnl is None else f"{pnl*100:+.1f}%"
            reasons = "; ".join((r.get("reasons") or [])[:2])
            lines.append(
                f"{r.get('symbol')}: {r.get('recommendation')} ({pnl_s})"
                + (f" - {reasons}" if reasons else "")
            )
        
        msg_body = (
            "<b>[TEST MOCK PORTFOLIO]</b>\n"
            "This is a diagnostic run showing how your positions are reviewed when active:\n\n"
            + "\n".join(lines)
        )
        
        print("[+] Sending mock portfolio alert to Telegram...")
        ok = telegram_notify.send_status(
            f"Test Portfolio Review: {len(reviews)} mock position(s)",
            msg_body,
            icon="PORTFOLIO 🧪"
        )
        if ok:
            print("[SUCCESS] Test Telegram message delivered successfully!")
        else:
            print("[FAILURE] Failed to send Telegram message. Check bot credentials.")
    else:
        print("[!] No reviews generated.")

if __name__ == "__main__":
    run_test()
