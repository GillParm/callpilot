import sqlite3

import pytest

from callpilot.db import connect, init_db


def test_init_db_is_idempotent() -> None:
    conn = connect(":memory:")
    init_db(conn)
    init_db(conn)
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"dentists", "patients", "slots", "appointments", "calls", "call_events"} <= tables


def test_database_rejects_double_booking() -> None:
    conn = connect(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO dentists VALUES ('d1', 'Dr. Test', 'dentist')")
    conn.execute("INSERT INTO patients VALUES ('p1', 'A', 'B', '1990-01-01', '555', NULL)")
    conn.execute("INSERT INTO patients VALUES ('p2', 'C', 'D', '1990-01-01', '555', NULL)")
    conn.execute("INSERT INTO slots VALUES ('s1', 'd1', '2030-01-01T14:00:00Z', 30)")
    insert = (
        "INSERT INTO appointments (id, patient_id, slot_id, reason, status, created_at) "
        "VALUES (?, ?, 's1', 'checkup', 'booked', '2030-01-01T00:00:00Z')"
    )
    conn.execute(insert, ("a1", "p1"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("a2", "p2"))
