"""Dated market snapshots shared by session messages and AI research."""

from __future__ import annotations

import math
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def _positive(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def chart_snapshot(symbol: str, chart: dict, now: datetime | None = None) -> dict:
    """Compare the regular quote with the immediately preceding daily bar.

    chartPreviousClose is a chart-range baseline, not a daily reference.
    Bar timestamps also identify overnight futures sessions without treating
    their midnight boundary as a new trading session.
    """
    now = now or datetime.now(timezone.utc)
    out = {"symbol": symbol, "source": "Yahoo", "status": "unavailable",
           "price": None, "pct": None, "quote_time": None,
           "reference_date": None, "reference_close": None}
    meta = chart.get("meta") or {}
    price = _positive(meta.get("regularMarketPrice"))
    quote_ts = _positive(meta.get("regularMarketTime"))
    if price is None or quote_ts is None:
        return out
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    quote_time = datetime.fromtimestamp(quote_ts, tz=timezone.utc)
    local_now = now.astimezone(tz)
    local_quote = quote_time.astimezone(tz)
    out.update(price=price, quote_time=quote_time.isoformat())

    quotes = (chart.get("indicators", {}).get("quote") or [{}])[0]
    bars = sorted((float(ts), _positive(close)) for ts, close in
                  zip(chart.get("timestamp") or [], quotes.get("close") or [])
                  if ts is not None and float(ts) <= quote_ts)
    has_current_bar = bool(bars) and (
        symbol.endswith("=F") or
        datetime.fromtimestamp(bars[-1][0], tz).date() == local_quote.date())
    if has_current_bar and len(bars) >= 2 and bars[-2][1] is not None:
        ref_ts, ref_close = bars[-2]
        out.update(pct=price / ref_close - 1.0, reference_close=ref_close,
                   reference_date=datetime.fromtimestamp(ref_ts, tz).date().isoformat())

    is_future = symbol.endswith("=F")
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start_ts, end_ts = _positive(regular.get("start")), _positive(regular.get("end"))
    start, end = time(9, 30), time(16)
    if start_ts and end_ts:
        start_dt = datetime.fromtimestamp(start_ts, tz)
        end_dt = datetime.fromtimestamp(end_ts, tz)
        if start_dt.date() == local_now.date() == end_dt.date():
            start, end = start_dt.time(), end_dt.time()
    if quote_time > now and (quote_time - now).total_seconds() > 300:
        out["status"] = "invalid timestamp"
    elif is_future:
        out["status"] = "live" if (now - quote_time).total_seconds() <= 1800 else "stale"
    elif local_now.weekday() >= 5:
        out["status"] = "market closed"
    elif local_now.time() < start:
        out["status"] = "premarket; last regular quote"
    elif local_quote.date() != local_now.date():
        out["status"] = "previous session"
    elif local_now.time() >= end:
        close_time = datetime.combine(local_now.date(), end, tzinfo=tz)
        out["status"] = "closed" if (close_time - quote_time).total_seconds() <= 1800 else "stale"
    elif (now - quote_time).total_seconds() > 1800:
        out["status"] = "stale"
    else:
        out["status"] = "live"
    return out


def usable_pct(snapshot: dict) -> float | None:
    return snapshot.get("pct") if snapshot.get("status") in {"live", "closed"} else None


def direction(pct: float | None) -> str:
    if pct is None:
        return "UNKNOWN"
    if pct > 0.005:
        return "BULLISH"
    if pct < -0.005:
        return "BEARISH"
    return "NEUTRAL"


def canadian_regime(snapshot: dict) -> str:
    status = snapshot.get("status", "unavailable")
    if status.startswith("premarket"):
        return "CANADA NOT OPEN"
    if status == "market closed":
        return "CANADA MARKET CLOSED"
    tag = direction(usable_pct(snapshot))
    return f"CANADA {tag}" + (f" ({status})" if tag == "UNKNOWN" else "")


def us_regime(qqq: dict, spy: dict) -> str:
    statuses = [snapshot.get("status", "unavailable") for snapshot in (qqq, spy)]
    if all(status.startswith("premarket") for status in statuses):
        return "US NOT OPEN"
    if all(status == "market closed" for status in statuses):
        return "US MARKET CLOSED"
    q, s = usable_pct(qqq), usable_pct(spy)
    if q is None or s is None:
        issues = []
        for symbol, snapshot in (("QQQ", qqq), ("SPY", spy)):
            if usable_pct(snapshot) is None:
                status = snapshot.get("status", "unavailable")
                reason = "daily comparison unavailable" if status in {"live", "closed"} else status
                issues.append(f"{symbol} {reason}")
        return "US UNKNOWN (" + "; ".join(issues) + ")"
    if q > 0.005 and s > 0.005:
        return "US BULLISH"
    if q < -0.005 or s < -0.005:
        return "US BEARISH"
    return "US NEUTRAL"


def percent_text(value) -> str:
    try:
        value = float(value)
        return f"{value * 100:+.2f}%" if math.isfinite(value) else "unavailable"
    except (TypeError, ValueError):
        return "unavailable"


def _snapshot_details(snapshot: dict, *, comparison: bool = True) -> str:
    quote = snapshot.get("quote_time")
    quote_label = (datetime.fromisoformat(quote).astimezone(
        ZoneInfo("America/Denver")).strftime("%m-%d %H:%M %Z") if quote else "unavailable")
    status = snapshot.get("status", "unavailable")
    # Older saved snapshots used "close" even for an intraday prior-session quote.
    if status == "premarket; last regular close":
        status = "premarket; last regular quote"
    details = f"{status}; quote {quote_label}"
    if comparison:
        details += f"; vs {snapshot.get('reference_date') or 'unavailable'}"
    return f"[{details}]"


def format_market_context(market: dict) -> str:
    lines = [f"Canadian regime: {market.get('regime', 'UNKNOWN')}",
             f"US context: {market.get('us_regime', 'UNKNOWN')}",
             f"Gold: {market.get('gold_tag', 'UNKNOWN')}"]
    if market.get("captured_at"):
        captured = datetime.fromisoformat(market["captured_at"])
        lines.append("Checked: " + captured.astimezone(ZoneInfo("America/Denver")).strftime(
            "%Y-%m-%d %H:%M %Z"))
    snapshots = market.get("snapshots") or {}
    for symbol, field in (("XIC.TO", "xic_pct"), ("SPY", "spy_pct"),
                          ("QQQ", "qqq_pct"), ("NQ=F", "nq_f_pct"),
                          ("GC=F", "gold_pct"), ("CL=F", "wti_pct")):
        snap = snapshots.get(symbol)
        if snap:
            lines.append(f"{symbol}: {percent_text(snap.get('pct'))} {_snapshot_details(snap)}")
        else:
            lines.append(f"{symbol}: {percent_text(market.get(field))}")
    vix = market.get("vix_level")
    vix_snapshot = snapshots.get("^VIX")
    if vix_snapshot:
        vix = vix_snapshot.get("price")
    vix_details = " " + _snapshot_details(vix_snapshot, comparison=False) if vix_snapshot else ""
    lines.append(f"VIX: {vix if vix is not None else 'unavailable'}{vix_details}")
    sectors = market.get("sector_etf_pct") or {}
    sector_symbols = dict.fromkeys([
        *sectors, *(symbol[:-3] for symbol in snapshots
                    if symbol.endswith(".TO") and symbol != "XIC.TO")])
    if sector_symbols:
        lines.append("Canadian sectors (dated quotes; pre-open values are reference only):")
        for symbol in sector_symbols:
            snap = snapshots.get(symbol + ".TO")
            if snap:
                # Display evidence, not the live-only values used by scoring gates.
                lines.append(f"{symbol}: {percent_text(snap.get('pct'))} {_snapshot_details(snap)}")
            else:
                lines.append(f"{symbol}: {percent_text(sectors.get(symbol))} [quote timing unavailable]")
    lines.append("Pre-open, stale and unavailable readings are not today's live market direction.")
    return "\n".join(lines)
