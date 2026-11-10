from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from callpilot.db import connect, init_db
from callpilot.db.seed import seed_all

TZ = ZoneInfo("America/Toronto")
# Tuesday morning, before the clinic opens.
NOW = datetime(2026, 11, 10, 8, 0, tzinfo=TZ)


@pytest.fixture
def tz() -> ZoneInfo:
    return TZ


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect(":memory:")
    init_db(connection)
    seed_all(connection, now=NOW, tz=TZ)
    yield connection
    connection.close()
