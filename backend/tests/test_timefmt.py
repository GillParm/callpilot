from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from callpilot.clinic.timefmt import from_utc_iso, spoken_datetime, to_utc_iso

TORONTO = ZoneInfo("America/Toronto")


def test_utc_round_trip_preserves_instant() -> None:
    local = datetime(2026, 11, 12, 9, 0, tzinfo=TORONTO)
    assert to_utc_iso(local) == "2026-11-12T14:00:00Z"
    assert from_utc_iso("2026-11-12T14:00:00Z") == local


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="aware"):
        to_utc_iso(datetime(2026, 1, 1, 9, 0))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 11, 12, 9, 0, tzinfo=TORONTO), "Thursday, November 12 at 9:00 AM"),
        (datetime(2026, 11, 14, 12, 30, tzinfo=TORONTO), "Saturday, November 14 at 12:30 PM"),
        (datetime(2026, 11, 16, 0, 15, tzinfo=UTC), "Monday, November 16 at 12:15 AM"),
    ],
)
def test_spoken_datetime(moment: datetime, expected: str) -> None:
    assert spoken_datetime(moment) == expected
