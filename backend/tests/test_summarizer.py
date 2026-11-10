from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from anthropic.types.beta import BetaTextBlock

from callpilot.calllog.summarizer import (
    AnthropicSummarizer,
    PlainSummarizer,
    create_summarizer,
    summarize_call,
)
from callpilot.config import Settings
from tests.test_calllog import make_booking_call


class FakeSummarizer:
    name = "fake"

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.seen: list[tuple[str, str]] = []

    async def summarize(self, transcript: str, outcome: str) -> str:
        self.seen.append((transcript, outcome))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def stored_summary(conn: sqlite3.Connection, call_id: str) -> str:
    return str(conn.execute("SELECT summary FROM calls WHERE id = ?", (call_id,)).fetchone()[0])


async def test_summary_is_written_to_the_call_log(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> None:
    call_id = await make_booking_call(conn, tz, now)
    summarizer = FakeSummarizer("Maria booked a cleaning for Wednesday at 9.")
    assert await summarize_call(conn, call_id, summarizer) == summarizer.reply
    assert stored_summary(conn, call_id) == summarizer.reply
    transcript, outcome = summarizer.seen[0]
    assert outcome == "booked"
    assert "Caller: Yes." in transcript


async def test_a_failing_model_falls_back_to_a_plain_summary(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> None:
    call_id = await make_booking_call(conn, tz, now)
    await summarize_call(conn, call_id, FakeSummarizer(TimeoutError("slow")))
    summary = stored_summary(conn, call_id)
    assert summary.startswith('The caller said: "A cleaning tomorrow morning?"')
    assert summary.endswith("An appointment was booked.")


async def test_unknown_calls_are_ignored(conn: sqlite3.Connection) -> None:
    assert await summarize_call(conn, "call_nope", PlainSummarizer()) is None


async def test_anthropic_summarizer_request_and_refusal_handling() -> None:
    requests: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> SimpleNamespace:
        requests.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[BetaTextBlock(type="text", text=" Booked a cleaning. ")],
        )

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    summarizer = AnthropicSummarizer(api_key="x", model="claude-opus-5-5", client=client)
    assert await summarizer.summarize("Caller: hi", "booked") == "Booked a cleaning."
    assert requests[0]["fallbacks"] == "default"
    assert requests[0]["output_config"] == {"effort": "low"}


def test_summarizer_selection() -> None:
    def settings(**keys: str) -> Settings:
        return Settings(_env_file=None, **keys)  # type: ignore[call-arg]

    assert create_summarizer(settings()).name == "plain"
    assert create_summarizer(settings(GEMINI_API_KEY="g")).name == "gemini"
    assert (
        create_summarizer(settings(GEMINI_API_KEY="g", ANTHROPIC_API_KEY="a")).name == "anthropic"
    )
    forced = settings(ANTHROPIC_API_KEY="a", SUMMARY_PROVIDER="plain")
    assert create_summarizer(forced).name == "plain"
