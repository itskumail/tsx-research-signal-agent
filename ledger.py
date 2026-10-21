"""
ledger.py
---------
Excel-based trading ledger. Single source of truth for every fill and P&L.

File: C:\\Trading\\trading_ledger.xlsx

Sheets:
    Dashboard     - headline numbers (capital, cumulative P&L, equity, trade count)
    Trades        - one row per fill (BUY/SELL, symbol, qty, price, $ amount, session)
    Positions     - current open positions with unrealized P&L
    DailySummary  - one row per trading day (realized P&L, wins/losses, notes)

Used by:
    place_order.py  - auto-logs every fill
    Session prompts - update Dashboard + DailySummary at session end
    CLI             - python ledger.py show | summary | recalc

Safe to import even if openpyxl is missing — functions no-op with a warning.
"""

from __future__ import annotations
import os
import sys
from datetime import datetime
from pathlib import Path

LEDGER_PATH = Path(r"C:\Trading\trading_ledger.xlsx")
TRADING_CAPITAL = 20000.00  # CAD — user's allocated tranche

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    _OPENPYXL_OK = True
except ImportError:
    _OPENPYXL_OK = False


# ----------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------
TRADES_HEADERS = [
    "Date", "Time", "Action", "Symbol", "Currency", "Qty",
    "OrderType", "LimitPrice", "FillPrice", "GrossAmount",
    "Session", "OrderId", "Status", "Notes",
]

POSITIONS_HEADERS = [
    "Symbol", "Currency", "Qty", "AvgCost", "CurrentPrice",
    "MarketValue", "UnrealizedPnL", "UnrealizedPct", "LastUpdated",
]

DAILY_HEADERS = [
    "Date", "StartingCash", "EndingCash", "RealizedPnL", "RealizedPct",
    "TradesCount", "Wins", "Losses", "WinRate", "Notes",
]

DASHBOARD_LAYOUT = [
    ("Trading Capital (CAD)", TRADING_CAPITAL),
    ("Cash on Hand (CAD)",    TRADING_CAPITAL),
    ("Open Positions Value",  0.0),
    ("Total Equity",          TRADING_CAPITAL),
    ("Cumulative Realized P&L", 0.0),
    ("Cumulative Realized %",   0.0),
    ("Total Trades (fills)",  0),
    ("Winning Trades",        0),
    ("Losing Trades",         0),
    ("Win Rate %",            0.0),
    ("Last Updated",          ""),
]


# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------
def _warn_missing():
    if not _OPENPYXL_OK:
        print("WARNING: openpyxl not installed — ledger updates will be skipped.")
        print("  Install: pip install openpyxl")


def ensure_ledger() -> bool:
    """Create the ledger file with sheets/headers if it doesn't exist. Returns True if OK."""
    if not _OPENPYXL_OK:
        _warn_missing()
        return False

    if LEDGER_PATH.exists():
        return True

    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # Dashboard
    ws = wb.active
    ws.title = "Dashboard"
    ws["A1"] = "IBKR Paper Trading — Live Ledger"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:B1")
    ws["A2"] = f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws["A2"].font = Font(italic=True, color="666666")
    ws.merge_cells("A2:B2")
    for i, (label, value) in enumerate(DASHBOARD_LAYOUT, start=4):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True)
        ws.cell(row=i, column=2, value=value)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 20

    # Trades
    ws = wb.create_sheet("Trades")
    for col, h in enumerate(TRADES_HEADERS, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")
        c.alignment = Alignment(horizontal="center")
    for col in range(1, len(TRADES_HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    ws.freeze_panes = "A2"

    # Positions
    ws = wb.create_sheet("Positions")
    for col, h in enumerate(POSITIONS_HEADERS, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="548235")
        c.alignment = Alignment(horizontal="center")
    for col in range(1, len(POSITIONS_HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    ws.freeze_panes = "A2"

    # Daily Summary
    ws = wb.create_sheet("DailySummary")
    for col, h in enumerate(DAILY_HEADERS, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="C65911")
        c.alignment = Alignment(horizontal="center")
    for col in range(1, len(DAILY_HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14
    ws.freeze_panes = "A2"

    wb.save(LEDGER_PATH)
    print(f"Created ledger: {LEDGER_PATH}")
    return True


# ----------------------------------------------------------------------
# Append a fill
# ----------------------------------------------------------------------
def log_fill(action: str, symbol: str, qty: int, fill_price: float | None,
             order_type: str = "MARKET", limit_price: float | None = None,
             currency: str = "CAD", session: str = "", order_id: str = "",
             status: str = "Submitted", notes: str = "") -> None:
    """Append a fill (or submission) row to the Trades sheet."""
    if not _OPENPYXL_OK:
        _warn_missing()
        return
    ensure_ledger()

    wb = load_workbook(LEDGER_PATH)
    ws = wb["Trades"]

    now = datetime.now()
    px = fill_price if fill_price is not None else (limit_price or 0.0)
    gross = round(qty * px, 2) if px else 0.0

    row = [
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S"),
        action.upper(),
        symbol.upper(),
        currency.upper(),
        qty,
        order_type.upper(),
        limit_price if limit_price is not None else "",
        fill_price if fill_price is not None else "",
        gross,
        session,
        order_id,
        status,
        notes,
    ]
    ws.append(row)

    # Update Dashboard trade count + last updated
    dash = wb["Dashboard"]
    for r in range(4, 4 + len(DASHBOARD_LAYOUT)):
        label = dash.cell(row=r, column=1).value
        if label == "Total Trades (fills)":
            dash.cell(row=r, column=2, value=(dash.cell(row=r, column=2).value or 0) + 1)
        if label == "Last Updated":
            dash.cell(row=r, column=2, value=now.strftime("%Y-%m-%d %H:%M:%S"))

    wb.save(LEDGER_PATH)
    print(f"Ledger: logged {action} {qty} {symbol} @ {px} ({status})")


# ----------------------------------------------------------------------
# Daily summary (called by Session 4 end-of-day)
# ----------------------------------------------------------------------
def log_daily_summary(date: str, starting_cash: float, ending_cash: float,
                      realized_pnl: float, trades_count: int,
                      wins: int, losses: int, notes: str = "") -> None:
    if not _OPENPYXL_OK:
        _warn_missing()
        return
    ensure_ledger()

    wb = load_workbook(LEDGER_PATH)
    ws = wb["DailySummary"]
    win_rate = round(100 * wins / max(wins + losses, 1), 2)
    pnl_pct  = round(100 * realized_pnl / TRADING_CAPITAL, 3)
    ws.append([date, starting_cash, ending_cash, realized_pnl, pnl_pct,
               trades_count, wins, losses, win_rate, notes])

    # Roll into Dashboard cumulative
    dash = wb["Dashboard"]
    cum_pnl = 0.0
    total_wins = 0
    total_losses = 0
    for r in range(2, ws.max_row + 1):
        cum_pnl += (ws.cell(row=r, column=4).value or 0)
        total_wins   += (ws.cell(row=r, column=7).value or 0)
        total_losses += (ws.cell(row=r, column=8).value or 0)

    for r in range(4, 4 + len(DASHBOARD_LAYOUT)):
        label = dash.cell(row=r, column=1).value
        if label == "Cumulative Realized P&L":
            dash.cell(row=r, column=2, value=round(cum_pnl, 2))
        elif label == "Cumulative Realized %":
            dash.cell(row=r, column=2, value=round(100 * cum_pnl / TRADING_CAPITAL, 3))
        elif label == "Winning Trades":
            dash.cell(row=r, column=2, value=total_wins)
        elif label == "Losing Trades":
            dash.cell(row=r, column=2, value=total_losses)
        elif label == "Win Rate %":
            dash.cell(row=r, column=2, value=round(100 * total_wins / max(total_wins + total_losses, 1), 2))
        elif label == "Cash on Hand (CAD)":
            dash.cell(row=r, column=2, value=ending_cash)
        elif label == "Total Equity":
            dash.cell(row=r, column=2, value=ending_cash)  # EOD is 100% cash
        elif label == "Last Updated":
            dash.cell(row=r, column=2, value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    wb.save(LEDGER_PATH)
    print(f"Ledger: logged daily summary for {date}, realized P&L {realized_pnl:+.2f} CAD")


# ----------------------------------------------------------------------
# Positions snapshot (called by any session after scanning)
# ----------------------------------------------------------------------
def update_positions(positions: list[dict]) -> None:
    """
    Overwrite the Positions sheet with a current snapshot.
    positions: list of dicts with keys:
        symbol, currency, qty, avg_cost, current_price
    """
    if not _OPENPYXL_OK:
        _warn_missing()
        return
    ensure_ledger()

    wb = load_workbook(LEDGER_PATH)
    ws = wb["Positions"]
    # clear data rows
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)

    open_value = 0.0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for p in positions:
        qty = p["qty"]
        avg = p["avg_cost"]
        cur = p.get("current_price") or 0.0
        mv  = round(qty * cur, 2)
        upnl = round((cur - avg) * qty, 2)
        upct = round(100 * (cur - avg) / avg, 3) if avg else 0.0
        open_value += mv
        ws.append([p["symbol"], p.get("currency", "CAD"), qty, avg, cur,
                   mv, upnl, upct, now])

    # Update Dashboard open-position value
    dash = wb["Dashboard"]
    cash = None
    for r in range(4, 4 + len(DASHBOARD_LAYOUT)):
        label = dash.cell(row=r, column=1).value
        if label == "Cash on Hand (CAD)":
            cash = dash.cell(row=r, column=2).value or 0
        if label == "Open Positions Value":
            dash.cell(row=r, column=2, value=round(open_value, 2))
        if label == "Last Updated":
            dash.cell(row=r, column=2, value=now)
    if cash is not None:
        for r in range(4, 4 + len(DASHBOARD_LAYOUT)):
            if dash.cell(row=r, column=1).value == "Total Equity":
                dash.cell(row=r, column=2, value=round(cash + open_value, 2))

    wb.save(LEDGER_PATH)
    print(f"Ledger: updated {len(positions)} position(s), open value {open_value:.2f} CAD")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _cmd_show():
    if not _OPENPYXL_OK:
        _warn_missing()
        return
    ensure_ledger()
    wb = load_workbook(LEDGER_PATH, data_only=True)
    print(f"\n=== Ledger: {LEDGER_PATH} ===\n")
    dash = wb["Dashboard"]
    for r in range(4, 4 + len(DASHBOARD_LAYOUT)):
        label = dash.cell(row=r, column=1).value
        value = dash.cell(row=r, column=2).value
        if label:
            print(f"  {label:<30} {value}")
    trades = wb["Trades"]
    print(f"\n  Trades logged: {trades.max_row - 1}")
    daily = wb["DailySummary"]
    print(f"  Days recorded: {daily.max_row - 1}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "init":
        ensure_ledger()
    elif cmd == "show":
        _cmd_show()
    else:
        print("Usage: python ledger.py [init|show]")
