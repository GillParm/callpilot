"""Writes one call's events to the call log as they happen.

Events are append-only rows with a wall-clock timestamp and the milliseconds
since the call started, which is what the timeline in the UI is drawn from.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from callpilot.clinic.timefmt import to_utc_iso


class CallRecorder:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        call_id: str,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._conn = conn
        self.call_id = call_id
        self._clock = clock
        self._monotonic = monotonic
        self._t0 = monotonic()

    def elapsed_ms(self) -> int:
        return int((self._monotonic() - self._t0) * 1000)

    def start(
        self,
        *,
        channel: str,
        mode: str,
        provider: str,
        model: str,
        caller_number: str | None = None,
    ) -> None:
        self._t0 = self._monotonic()
        self._conn.execute(
            "INSERT INTO calls (id, channel, mode, provider, model, started_at, caller_number) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self.call_id,
                channel,
                mode,
                provider,
                model,
                to_utc_iso(self._clock()),
                caller_number,
            ),
        )

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO call_events (call_id, at, elapsed_ms, type, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                self.call_id,
                to_utc_iso(self._clock()),
                self.elapsed_ms(),
                event_type,
                json.dumps(payload, default=str),
            ),
        )

    def finish(self, *, outcome: str) -> None:
        self._conn.execute(
            "UPDATE calls SET ended_at = ?, outcome = ? WHERE id = ? AND ended_at IS NULL",
            (to_utc_iso(self._clock()), outcome, self.call_id),
        )

    def set_summary(self, summary: str) -> None:
        self._conn.execute("UPDATE calls SET summary = ? WHERE id = ?", (summary, self.call_id))
