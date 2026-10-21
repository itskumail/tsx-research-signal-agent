"""
dashboard.py
------------
Read-only mirror of the live trading state. Generates:

  C:\\Trading\\dashboard.html   — single-file HTML dashboard
  C:\\Trading\\dashboard.xlsx   — Excel mirror with multiple sheets

Sources:
  • IBKR Gateway  — real positions, account equity (read-only)
  • logs/signals_sent_*.jsonl — historical signals dispatched
  • logs/news_events_*.jsonl  — news listener output
  • logs/skipped_*.json       — Gateway-down sessions

Usage:
  python dashboard.py            # render both HTML + Excel
  python dashboard.py --html     # HTML only
  python dashboard.py --excel    # Excel only
  python dashboard.py --open     # render then open dashboard.html
"""
from __future__ import annotations
import json
import sys
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

TRADING_DIR = Path(r"C:\Trading")
LOGS_DIR    = TRADING_DIR / "logs"
HTML_PATH   = TRADING_DIR / "dashboard.html"
XLSX_PATH   = TRADING_DIR / "dashboard.xlsx"


# ---------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------
def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def load_signals_window(days: int = 30) -> list[dict]:
    """Concatenate signals_sent_<date>.jsonl for the last N days."""
    out = []
    today = datetime.now().date()
    for i in range(days):
        d = today - timedelta(days=i)
        path = LOGS_DIR / f"signals_sent_{d.strftime('%Y%m%d')}.jsonl"
        out.extend(load_jsonl(path))
    return sorted(out, key=lambda r: r.get("timestamp", ""), reverse=True)


def load_news_window(days: int = 7) -> list[dict]:
    out = []
    today = datetime.now().date()
    for i in range(days):
        d = today - timedelta(days=i)
        path = LOGS_DIR / f"news_events_{d.strftime('%Y%m%d')}.jsonl"
        out.extend(load_jsonl(path))
    return sorted(out, key=lambda r: r.get("timestamp", ""), reverse=True)


def fetch_ibkr_state() -> dict:
    """Best-effort: pull live positions + account summary from Gateway."""
    state = {"connected": False, "positions": [], "account": {}, "error": None}
    try:
        from ibkr_client import IBKRClient
        c = IBKRClient()
        c.connect()
        try:
            state["connected"] = True
            state["positions"] = c.get_portfolio() or []
            try:
                state["account"] = c.get_account_summary() or {}
            except Exception:
                pass
        finally:
            c.disconnect()
    except Exception as e:
        state["error"] = str(e)
    return state


def latest_regime() -> dict:
    """Best-effort: live regime + gold tag for header."""
    try:
        from session_runner import determine_regime
        regime, gold, market = determine_regime()
        return {"regime": regime, "gold": gold, "market": market}
    except Exception as e:
        return {"regime": "—", "gold": "—", "market": {}, "error": str(e)}


# ---------------------------------------------------------------
# Stats
# ---------------------------------------------------------------
def signal_stats(signals: list[dict]) -> dict:
    if not signals:
        return {"total": 0, "by_symbol": {}, "by_session": {}}
    by_symbol = defaultdict(int)
    by_session = defaultdict(int)
    by_score = defaultdict(int)
    for s in signals:
        by_symbol[s.get("symbol", "—")] += 1
        by_session[s.get("session", "—")] += 1
        by_score[s.get("score", 0)] += 1
    return {
        "total": len(signals),
        "by_symbol": dict(sorted(by_symbol.items(), key=lambda x: -x[1])),
        "by_session": dict(by_session),
        "by_score": dict(sorted(by_score.items())),
    }


# ---------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>IBKR Trading Bot — Dashboard</title>
<style>
:root {
  --bg: #0e1116; --panel: #161b22; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
  --green: #3fb950; --red: #f85149; --yellow: #d29922;
}
body { background: var(--bg); color: var(--text); margin: 0; padding: 20px;
       font-family: -apple-system, "SF Pro Display", "Segoe UI", sans-serif; }
header { border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 16px; }
h1 { margin: 0; font-size: 1.5rem; }
.muted { color: var(--muted); font-size: 0.85rem; }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
         padding: 16px; margin-bottom: 16px; }
.panel h2 { margin: 0 0 10px 0; font-size: 1.05rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap: 10px; }
.kv { background: #0d1117; padding: 10px; border-radius: 6px; border: 1px solid var(--border); }
.kv .k { color: var(--muted); font-size: 0.8rem; }
.kv .v { font-size: 1.15rem; font-weight: 600; }
.green { color: var(--green); } .red { color: var(--red); } .yellow { color: var(--yellow); }
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 500; }
tr:hover td { background: rgba(255,255,255,0.02); }
.tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
.tag-buy   { background: rgba(63,185,80,0.15);  color: var(--green); }
.tag-hold  { background: rgba(210,153,34,0.15); color: var(--yellow); }
.tag-news  { background: rgba(88,166,255,0.15); color: var(--accent); }
.empty { color: var(--muted); font-style: italic; padding: 12px 0; }
.right { text-align: right; }
small.timestamp { color: var(--muted); }
</style>
</head>
<body>

<header>
<h1>📊 IBKR Trading Bot — Dashboard</h1>
<div class="muted">Generated {generated_at} • Strategy v4.2 • Canadian watchlist (signals-only)</div>
</header>

<!-- ============================ SNAPSHOT ============================ -->
<section class="panel">
<h2>Today's Snapshot</h2>
<div class="grid">
  <div class="kv"><div class="k">Regime</div><div class="v">{regime}</div></div>
  <div class="kv"><div class="k">Gold tag</div><div class="v">{gold}</div></div>
  <div class="kv"><div class="k">VIX</div><div class="v">{vix}</div></div>
  <div class="kv"><div class="k">QQQ</div><div class="v {qqq_class}">{qqq}</div></div>
  <div class="kv"><div class="k">SPY</div><div class="v {spy_class}">{spy}</div></div>
  <div class="kv"><div class="k">XIC</div><div class="v {xic_class}">{xic}</div></div>
  <div class="kv"><div class="k">WTI</div><div class="v {wti_class}">{wti}</div></div>
  <div class="kv"><div class="k">Signals today</div><div class="v">{signals_today}</div></div>
</div>
</section>

<!-- ============================ PORTFOLIO ============================ -->
<section class="panel">
<h2>Live Portfolio (IBKR)</h2>
{portfolio_html}
</section>

<!-- ============================ SIGNALS ============================ -->
<section class="panel">
<h2>Signals — Last 30 Days</h2>
{signals_html}
</section>

<!-- ============================ NEWS ============================ -->
<section class="panel">
<h2>News Events — Last 7 Days</h2>
{news_html}
</section>

<!-- ============================ SECTOR TAPE ============================ -->
<section class="panel">
<h2>Canadian Sector ETF Tape (Today)</h2>
<div class="grid">
{sector_html}
</div>
</section>

<!-- ============================ FOOTER ============================ -->
<footer class="muted" style="text-align:center; padding: 16px 0; font-size:0.8rem;">
  Read-only account snapshot. Research and Telegram alerts only.
  See <code>strategies/active_strategy.md</code> for the full strategy.
</footer>

</body>
</html>
"""


def _color_class(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct > 0.001:  return "green"
    if pct < -0.001: return "red"
    return ""


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"{pct*100:+.2f}%"


def _portfolio_html(positions: list, error: str | None) -> str:
    if error:
        return f'<div class="empty">Could not connect to IB Gateway: {error}</div>'
    if not positions:
        return '<div class="empty">No open positions.</div>'
    rows = []
    for p in positions:
        sym = p.get("symbol", "—")
        qty = p.get("position", p.get("qty", 0))
        avg = p.get("avg_cost", p.get("avgCost", 0))
        mkt = p.get("market_price", p.get("marketPrice", 0))
        upl = p.get("unrealized_pnl", p.get("unrealizedPNL", 0))
        try: pct = (float(mkt) - float(avg)) / float(avg) * 100 if avg else 0.0
        except Exception: pct = 0.0
        cls = "green" if pct > 0 else ("red" if pct < 0 else "")
        rows.append(
            f"<tr><td>{sym}</td><td class='right'>{qty}</td>"
            f"<td class='right'>${float(avg):.2f}</td>"
            f"<td class='right'>${float(mkt):.2f}</td>"
            f"<td class='right {cls}'>{pct:+.2f}%</td>"
            f"<td class='right {cls}'>${float(upl):,.2f}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Symbol</th><th class='right'>Qty</th>"
        "<th class='right'>Avg Cost</th><th class='right'>Market</th>"
        "<th class='right'>P&L %</th><th class='right'>Unrealized $</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _signals_html(signals: list[dict]) -> str:
    if not signals:
        return '<div class="empty">No signals dispatched in the last 30 days.</div>'
    rows = []
    for s in signals[:50]:
        ts = s.get("timestamp", "")[:19].replace("T", " ")
        rows.append(
            f"<tr><td><small class='timestamp'>{ts}</small></td>"
            f"<td><b>{s.get('symbol','—')}</b></td>"
            f"<td><span class='tag tag-buy'>{s.get('action','—')}</span></td>"
            f"<td>{s.get('timeframe','—')}</td>"
            f"<td class='right'>${s.get('entry_price',0):.2f}</td>"
            f"<td class='right red'>${s.get('stop_loss',0):.2f}</td>"
            f"<td class='right green'>${s.get('take_profit',0):.2f}</td>"
            f"<td class='right'>{s.get('score','—')}/7</td>"
            f"<td>{s.get('session','—')}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Time</th><th>Symbol</th><th>Action</th>"
        "<th>TF</th><th class='right'>Entry</th><th class='right'>Stop</th>"
        "<th class='right'>Target</th><th class='right'>Score</th>"
        "<th>Session</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def _news_html(news: list[dict]) -> str:
    if not news:
        return '<div class="empty">No news events captured. (News listener may not be running yet — Phase 4.)</div>'
    rows = []
    for n in news[:30]:
        ts = n.get("timestamp", "")[:19].replace("T", " ")
        sentiment = n.get("sentiment", 0)
        sent_icon = {1: "🟢", -1: "🔴", 0: "⚪"}.get(sentiment, "")
        tickers = ", ".join(n.get("tickers", []))
        rows.append(
            f"<tr><td><small class='timestamp'>{ts}</small></td>"
            f"<td>{sent_icon}</td>"
            f"<td><b>{tickers}</b></td>"
            f"<td>{n.get('source','—')}</td>"
            f"<td>{n.get('headline','')[:120]}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Time</th><th></th><th>Tickers</th>"
        "<th>Source</th><th>Headline</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def _sector_html(market: dict) -> str:
    sectors = market.get("sector_etf_pct") or {}
    if not sectors:
        return '<div class="kv"><div class="k">No sector data</div><div class="v">—</div></div>'
    out = []
    for name, pct in sectors.items():
        cls = _color_class(pct)
        out.append(
            f'<div class="kv"><div class="k">{name}</div>'
            f'<div class="v {cls}">{_fmt_pct(pct)}</div></div>'
        )
    return "".join(out)


def render_html() -> Path:
    signals = load_signals_window(30)
    news    = load_news_window(7)
    state   = fetch_ibkr_state()
    regime  = latest_regime()
    market  = regime.get("market", {})

    # Today's signal count
    today = datetime.now().strftime("%Y%m%d")
    signals_today = sum(
        1 for s in signals
        if s.get("timestamp", "").startswith(datetime.now().date().isoformat())
    )

    replacements = {
        "{generated_at}":   datetime.now().strftime("%Y-%m-%d %H:%M:%S MDT"),
        "{regime}":         regime.get("regime", "—"),
        "{gold}":           regime.get("gold", "—"),
        "{vix}":            f"{market.get('vix_level'):.2f}" if market.get("vix_level") else "—",
        "{qqq}":            _fmt_pct(market.get("qqq_pct")),
        "{qqq_class}":      _color_class(market.get("qqq_pct")),
        "{spy}":            _fmt_pct(market.get("spy_pct")),
        "{spy_class}":      _color_class(market.get("spy_pct")),
        "{xic}":            _fmt_pct(market.get("xic_pct")),
        "{xic_class}":      _color_class(market.get("xic_pct")),
        "{wti}":            _fmt_pct(market.get("wti_pct")),
        "{wti_class}":      _color_class(market.get("wti_pct")),
        "{signals_today}":  str(signals_today),
        "{portfolio_html}": _portfolio_html(state["positions"], state["error"]),
        "{signals_html}":   _signals_html(signals),
        "{news_html}":      _news_html(news),
        "{sector_html}":    _sector_html(market),
    }
    html = HTML_TEMPLATE
    for k, v in replacements.items():
        html = html.replace(k, str(v))
    HTML_PATH.write_text(html, encoding="utf-8")
    return HTML_PATH


# ---------------------------------------------------------------
# Excel rendering
# ---------------------------------------------------------------
def render_excel() -> Path:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        print("openpyxl not installed; skipping Excel render.", file=sys.stderr)
        return None

    signals = load_signals_window(30)
    news    = load_news_window(7)
    state   = fetch_ibkr_state()
    regime  = latest_regime()
    market  = regime.get("market", {})

    wb = Workbook()

    # Snapshot
    ws = wb.active
    ws.title = "Snapshot"
    ws["A1"] = "IBKR Trading Bot — Dashboard"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A3"] = "Generated";  ws["B3"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws["A4"] = "Regime";     ws["B4"] = regime.get("regime", "—")
    ws["A5"] = "Gold tag";   ws["B5"] = regime.get("gold", "—")
    ws["A6"] = "VIX";        ws["B6"] = market.get("vix_level")
    ws["A7"] = "QQQ %";      ws["B7"] = market.get("qqq_pct")
    ws["A8"] = "SPY %";      ws["B8"] = market.get("spy_pct")
    ws["A9"] = "XIC %";      ws["B9"] = market.get("xic_pct")
    ws["A10"] = "WTI %";     ws["B10"] = market.get("wti_pct")
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 24

    # Portfolio
    ws = wb.create_sheet("Portfolio")
    headers = ["Symbol", "Qty", "Avg Cost", "Market Price", "P&L %", "Unrealized $"]
    for j, h in enumerate(headers, 1):
        ws.cell(row=1, column=j, value=h).font = Font(bold=True)
    if state["positions"]:
        for i, p in enumerate(state["positions"], 2):
            ws.cell(row=i, column=1, value=p.get("symbol"))
            ws.cell(row=i, column=2, value=p.get("position", p.get("qty")))
            ws.cell(row=i, column=3, value=p.get("avg_cost", p.get("avgCost")))
            ws.cell(row=i, column=4, value=p.get("market_price", p.get("marketPrice")))
            avg = float(p.get("avg_cost", p.get("avgCost", 0)) or 0)
            mkt = float(p.get("market_price", p.get("marketPrice", 0)) or 0)
            pct = (mkt - avg) / avg * 100 if avg else 0
            ws.cell(row=i, column=5, value=pct)
            ws.cell(row=i, column=6, value=p.get("unrealized_pnl", p.get("unrealizedPNL")))

    # Signals
    ws = wb.create_sheet("Signals 30d")
    headers = ["Timestamp", "Symbol", "Action", "Timeframe", "Entry", "Stop", "Target", "Score", "Confidence", "Session"]
    for j, h in enumerate(headers, 1):
        ws.cell(row=1, column=j, value=h).font = Font(bold=True)
    for i, s in enumerate(signals, 2):
        ws.cell(row=i, column=1,  value=s.get("timestamp", "")[:19])
        ws.cell(row=i, column=2,  value=s.get("symbol"))
        ws.cell(row=i, column=3,  value=s.get("action"))
        ws.cell(row=i, column=4,  value=s.get("timeframe"))
        ws.cell(row=i, column=5,  value=s.get("entry_price"))
        ws.cell(row=i, column=6,  value=s.get("stop_loss"))
        ws.cell(row=i, column=7,  value=s.get("take_profit"))
        ws.cell(row=i, column=8,  value=s.get("score"))
        ws.cell(row=i, column=9,  value=s.get("confidence"))
        ws.cell(row=i, column=10, value=s.get("session"))

    # News
    ws = wb.create_sheet("News 7d")
    headers = ["Timestamp", "Sentiment", "Tickers", "Source", "Headline"]
    for j, h in enumerate(headers, 1):
        ws.cell(row=1, column=j, value=h).font = Font(bold=True)
    for i, n in enumerate(news, 2):
        ws.cell(row=i, column=1, value=n.get("timestamp", "")[:19])
        ws.cell(row=i, column=2, value=n.get("sentiment"))
        ws.cell(row=i, column=3, value=", ".join(n.get("tickers", [])))
        ws.cell(row=i, column=4, value=n.get("source"))
        ws.cell(row=i, column=5, value=n.get("headline"))

    # Sectors
    ws = wb.create_sheet("Sectors")
    ws.cell(row=1, column=1, value="Sector ETF").font = Font(bold=True)
    ws.cell(row=1, column=2, value="% Change").font = Font(bold=True)
    sectors = market.get("sector_etf_pct") or {}
    for i, (name, pct) in enumerate(sectors.items(), 2):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=pct)

    wb.save(XLSX_PATH)
    return XLSX_PATH


# ---------------------------------------------------------------
# Entry
# ---------------------------------------------------------------
if __name__ == "__main__":
    do_html  = "--excel" not in sys.argv
    do_excel = "--html"  not in sys.argv

    if do_html:
        p = render_html()
        print(f"[OK] HTML: {p}")
    if do_excel:
        p = render_excel()
        if p: print(f"[OK] Excel: {p}")

    if "--open" in sys.argv:
        webbrowser.open(str(HTML_PATH))
