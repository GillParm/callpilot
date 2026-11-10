"""Deterministic fake data for the clinic.

Everything is generated from fixed seeds, so two machines seeded on the same day
see the same patients, slots and bookings. Slots are generated relative to
"today", and `ensure_slots` can be called on every start-up to keep a rolling
window of future availability without touching what already exists.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from callpilot.clinic.profile import CLINIC, SLOT_MINUTES, StaffMember
from callpilot.clinic.timefmt import to_utc_iso

# Demo patients documented in the README so anyone can try a full call.
DEMO_PATIENTS: tuple[tuple[str, str, str, str], ...] = (
    ("P-1001", "Maria", "Lopez", "1988-03-14"),
    ("P-1002", "James", "Carter", "1975-11-02"),
    ("P-1003", "Aisha", "Rahman", "1992-07-21"),
    ("P-1004", "Tom", "Nguyen", "2001-01-30"),
)

_FIRST_NAMES = (
    "Olivia", "Liam", "Emma", "Noah", "Ava", "Ethan", "Sofia", "Lucas", "Mia", "Mateo",
    "Chloe", "Arjun", "Zara", "Daniel", "Hana", "Owen", "Leah", "Samuel", "Nora", "Isaac",
)  # fmt: skip
_LAST_NAMES = (
    "Bennett", "Chen", "Okoye", "Silva", "Kowalski", "Haddad", "Fraser", "Ito", "Murphy",
    "Patel", "Dubois", "Novak", "Andersen", "Romero", "Kim", "Walsh", "Mensah", "Larsen",
)  # fmt: skip

GENERATED_PATIENTS = 36
PREBOOK_PROBABILITY = 0.55
_SEED = 20261110


def seed_reference_data(conn: sqlite3.Connection) -> None:
    """Insert staff and patients if they are missing. Safe to call repeatedly."""
    conn.executemany(
        "INSERT OR IGNORE INTO dentists (id, name, role) VALUES (?, ?, ?)",
        [(s.id, s.name, s.role) for s in CLINIC.staff],
    )
    rng = random.Random(_SEED)
    rows = [
        (pid, first, last, dob, f"555-01{index:02d}", None)
        for index, (pid, first, last, dob) in enumerate(DEMO_PATIENTS)
    ]
    for index in range(GENERATED_PATIENTS):
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        dob = date(1945, 1, 1) + timedelta(days=rng.randrange(0, 70 * 365))
        number = len(DEMO_PATIENTS) + index
        rows.append(
            (f"P-{1100 + index}", first, last, dob.isoformat(), f"555-01{number:02d}", None)
        )
    conn.executemany(
        "INSERT OR IGNORE INTO patients "
        "(id, first_name, last_name, date_of_birth, phone, email) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def _working_hours(weekday: int) -> list[tuple[time, time]]:
    """Clinic opening blocks for an ISO weekday (Mon=1)."""
    if weekday <= 5:
        return [(time(9), time(12)), (time(13), time(17))]
    if weekday == 6:
        return [(time(9), time(13))]
    return []


def _slot_starts(day: date, staff: StaffMember, tz: ZoneInfo) -> list[datetime]:
    if day.isoweekday() not in staff.workdays:
        return []
    step = timedelta(minutes=SLOT_MINUTES[staff.role])
    starts: list[datetime] = []
    for open_at, close_at in _working_hours(day.isoweekday()):
        cursor = datetime.combine(day, open_at, tzinfo=tz)
        end = datetime.combine(day, close_at, tzinfo=tz)
        while cursor + step <= end:
            starts.append(cursor)
            cursor += step
    return starts


def ensure_slots(
    conn: sqlite3.Connection,
    *,
    start: date,
    days: int,
    tz: ZoneInfo,
    now: datetime,
) -> int:
    """Make sure every working day in `[start, start + days)` has slots.

    New slots are pre-booked at random (deterministically, per slot) by
    generated patients so the schedule looks like a real, busy clinic.
    Returns the number of slots created.
    """
    patient_ids = [
        row["id"] for row in conn.execute("SELECT id FROM patients WHERE id >= 'P-1100'")
    ]
    created = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        for staff in CLINIC.staff:
            for slot_start in _slot_starts(day, staff, tz):
                slot_id = f"S-{slot_start:%Y%m%d-%H%M}-{staff.id}"
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO slots (id, dentist_id, starts_at, duration_minutes) "
                    "VALUES (?, ?, ?, ?)",
                    (slot_id, staff.id, to_utc_iso(slot_start), SLOT_MINUTES[staff.role]),
                )
                if cursor.rowcount != 1:
                    continue
                created += 1
                rng = random.Random(slot_id)
                if patient_ids and rng.random() < PREBOOK_PROBABILITY:
                    conn.execute(
                        "INSERT INTO appointments "
                        "(id, patient_id, slot_id, reason, status, created_at) "
                        "VALUES (?, ?, ?, ?, 'booked', ?)",
                        (
                            f"A-{slot_id[2:]}",
                            rng.choice(patient_ids),
                            slot_id,
                            "routine visit",
                            to_utc_iso(now),
                        ),
                    )
    return created


def seed_demo_appointment(conn: sqlite3.Connection, *, now: datetime) -> None:
    """Give the first demo patient an upcoming cleaning so cancellation can be tried."""
    exists = conn.execute(
        "SELECT 1 FROM appointments WHERE patient_id = 'P-1001' AND status = 'booked'"
    ).fetchone()
    if exists:
        return
    slot = conn.execute(
        """
        SELECT s.id FROM slots s
        WHERE s.dentist_id = 'hyg-shah' AND s.starts_at >= ?
          AND NOT EXISTS (
            SELECT 1 FROM appointments a WHERE a.slot_id = s.id AND a.status = 'booked'
          )
        ORDER BY s.starts_at LIMIT 1
        """,
        (to_utc_iso(now + timedelta(days=5)),),
    ).fetchone()
    if slot is None:
        return
    conn.execute(
        "INSERT INTO appointments (id, patient_id, slot_id, reason, status, created_at) "
        "VALUES (?, 'P-1001', ?, 'cleaning', 'booked', ?)",
        (f"A-DEMO-{slot['id'][2:]}", slot["id"], to_utc_iso(now)),
    )


def seed_all(conn: sqlite3.Connection, *, now: datetime, tz: ZoneInfo, days: int = 21) -> None:
    """Everything the app needs on start-up."""
    seed_reference_data(conn)
    ensure_slots(conn, start=now.astimezone(tz).date(), days=days, tz=tz, now=now)
    seed_demo_appointment(conn, now=now)
