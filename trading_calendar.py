"""Exchange sessions for Canadian signal windows and earnings horizons."""

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


@lru_cache(maxsize=2)
def calendar(currency: str = "CAD"):
    import exchange_calendars
    return exchange_calendars.get_calendar("XTSE" if currency == "CAD" else "XNYS")


def signal_window(now: datetime | None = None) -> tuple[bool, str | None]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("America/Denver"))
    now = now.astimezone(ZoneInfo("America/Toronto"))
    try:
        cal = calendar()
        day = now.date().isoformat()
        if not cal.is_session(day):
            return False, "TSX closed (weekend or exchange holiday)"
        opening = cal.session_open(day).to_pydatetime()
        closing = cal.session_close(day).to_pydatetime()
        if now < opening or now >= closing:
            return False, "outside TSX trading hours"
        if now < opening + timedelta(minutes=30):
            return False, "first 30 minutes of TSX session"
        if now >= closing - timedelta(minutes=15):
            return False, "last 15 minutes of TSX session"
        return True, None
    except Exception:
        return False, "TSX calendar unavailable; analysis only"


def earnings_within(earnings: date, now: datetime, days: int = 3, currency: str = "CAD") -> bool:
    today = now.astimezone(ZoneInfo("America/Toronto")).date()
    if earnings < today:
        return False
    cal = calendar(currency)
    count = len(cal.sessions_in_range(today.isoformat(), earnings.isoformat()))
    return count <= days + int(cal.is_session(today.isoformat()))
