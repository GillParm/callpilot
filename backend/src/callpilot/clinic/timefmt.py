"""Time helpers. Slots are stored in UTC and spoken in the clinic's local time."""

from __future__ import annotations

from datetime import UTC, datetime

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def to_utc_iso(moment: datetime) -> str:
    """Serialise an aware datetime as `YYYY-MM-DDTHH:MM:SSZ` (sortable as text)."""
    if moment.tzinfo is None:
        raise ValueError("expected an aware datetime")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_utc_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def spoken_time(moment: datetime) -> str:
    """`9:00 AM`, `12:30 PM`."""
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {suffix}"


def spoken_datetime(moment: datetime) -> str:
    """`Thursday, November 12 at 9:00 AM`: how a receptionist would say it."""
    weekday = _WEEKDAYS[moment.weekday()]
    month = _MONTHS[moment.month - 1]
    return f"{weekday}, {month} {moment.day} at {spoken_time(moment)}"
