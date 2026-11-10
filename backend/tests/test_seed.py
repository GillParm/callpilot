import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from callpilot.db import connect, init_db
from callpilot.db.seed import ensure_slots, seed_all


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def test_seed_creates_staff_patients_and_slots(conn: sqlite3.Connection) -> None:
    assert _count(conn, "SELECT COUNT(*) FROM dentists") == 4
    assert _count(conn, "SELECT COUNT(*) FROM patients") == 40
    assert _count(conn, "SELECT COUNT(*) FROM slots") > 200


def test_seed_leaves_a_realistic_mix_of_free_and_booked_slots(conn: sqlite3.Connection) -> None:
    total = _count(conn, "SELECT COUNT(*) FROM slots")
    booked = _count(conn, "SELECT COUNT(*) FROM appointments WHERE status = 'booked'")
    assert 0.4 < booked / total < 0.7


def test_no_slots_on_sundays(conn: sqlite3.Connection) -> None:
    # Slot ids encode the local date: S-YYYYMMDD-HHMM-<staff>.
    ids = [row[0] for row in conn.execute("SELECT id FROM slots")]
    days = {datetime.strptime(i.split("-")[1], "%Y%m%d").isoweekday() for i in ids}
    assert 7 not in days


def test_demo_patient_has_an_upcoming_appointment(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT reason FROM appointments WHERE patient_id = 'P-1001' AND status = 'booked'"
    ).fetchone()
    assert row is not None
    assert row["reason"] == "cleaning"


def test_seeding_is_deterministic_and_idempotent(now: datetime, tz: ZoneInfo) -> None:
    def snapshot() -> list[tuple[str, str]]:
        conn = connect(":memory:")
        init_db(conn)
        seed_all(conn, now=now, tz=tz)
        seed_all(conn, now=now, tz=tz)
        return [
            (row["slot_id"], row["patient_id"])
            for row in conn.execute("SELECT slot_id, patient_id FROM appointments ORDER BY slot_id")
        ]

    assert snapshot() == snapshot()


def test_ensure_slots_only_adds_missing_days(
    conn: sqlite3.Connection, now: datetime, tz: ZoneInfo
) -> None:
    assert ensure_slots(conn, start=now.date(), days=21, tz=tz, now=now) == 0
    assert ensure_slots(conn, start=now.date(), days=22, tz=tz, now=now) > 0
