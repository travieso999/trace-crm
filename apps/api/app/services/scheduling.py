"""Sending windows, business-day arithmetic and time-zone handling.

A schedule is ``{"days": [0..6], "start": "HH:MM", "end": "HH:MM", "timezone": "Area/City"}``
where days use Python's weekday numbering (Monday = 0).
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/New_York"


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _hhmm(value: str, default: time) -> time:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except Exception:
        return default


def window_parts(schedule: dict, tz_override: str | None = None) -> tuple[ZoneInfo, set[int], time, time]:
    tz = _tz(tz_override or schedule.get("timezone"))
    days = set(schedule.get("days") or [0, 1, 2, 3, 4])
    start = _hhmm(schedule.get("start", "09:00"), time(9))
    end = _hhmm(schedule.get("end", "17:00"), time(17))
    if end <= start:
        end = time(23, 59)
    return tz, days, start, end


def in_window(at: datetime, schedule: dict, tz_override: str | None = None) -> bool:
    tz, days, start, end = window_parts(schedule, tz_override)
    local = at.astimezone(tz)
    return local.weekday() in days and start <= local.time() < end


def next_window_start(at: datetime, schedule: dict, tz_override: str | None = None) -> datetime:
    """Earliest instant >= ``at`` inside the window."""
    tz, days, start, end = window_parts(schedule, tz_override)
    if not days:
        raise ValueError("Schedule has no sending days")
    local = at.astimezone(tz)
    for offset in range(0, 8):
        day = (local + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        open_at = datetime.combine(day, start, tzinfo=tz)
        close_at = datetime.combine(day, end, tzinfo=tz)
        if offset == 0 and local >= close_at:
            continue
        candidate = max(open_at, local) if offset == 0 else open_at
        return candidate.astimezone(UTC)
    raise ValueError("No sending window found")  # unreachable with non-empty days


def next_joint_window(at: datetime, windows: list[tuple[dict, str | None]]) -> datetime:
    """Earliest instant >= ``at`` that satisfies every (schedule, tz_override) window."""
    t = at
    for _ in range(30):
        moved = False
        for schedule, tz in windows:
            nt = next_window_start(t, schedule, tz)
            if nt != t:
                t, moved = nt, True
        if not moved:
            return t
    return t


def add_delay(start: datetime, days: int, *, business_days: bool, tz_name: str | None, sending_days: set[int]) -> datetime:
    """Add ``days`` calendar days, or ``days`` business days counted in ``tz_name`` (skipping non-sending days)."""
    if days <= 0:
        return start
    if not business_days:
        return start + timedelta(days=days)
    tz = _tz(tz_name)
    local = start.astimezone(tz)
    allowed = sending_days or {0, 1, 2, 3, 4}
    remaining = days
    while remaining > 0:
        local = local + timedelta(days=1)
        if local.weekday() in allowed:
            remaining -= 1
    return local.astimezone(UTC)


def start_of_local_day(at: datetime, tz_name: str | None) -> datetime:
    tz = _tz(tz_name)
    local = at.astimezone(tz)
    return datetime.combine(local.date(), time(0), tzinfo=tz).astimezone(UTC)
