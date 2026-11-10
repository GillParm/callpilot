"""SQLite connection helpers.

SQLite is plenty for a single clinic: tool calls are point lookups that finish in
well under a millisecond, and WAL mode lets the call log write while the API reads.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a connection with the pragmas every caller expects."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist yet."""
    schema = resources.files("callpilot.db").joinpath("schema.sql").read_text()
    conn.executescript(schema)
