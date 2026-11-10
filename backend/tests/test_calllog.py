from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from callpilot.calllog.store import call_stats, get_call, list_calls, percentile, transcript_text
from callpilot.providers.scripted import ScriptedProvider
from callpilot.session.factory import build_session
from tests.scripts import BOOKING_SCRIPT, SimulatedCaller
from tests.support import RecordingTransport


async def make_booking_call(conn: sqlite3.Connection, tz: ZoneInfo, now: datetime) -> str:
    transport = RecordingTransport()
    session, config = build_session(
        conn=conn,
        tz=tz,
        provider=ScriptedProvider(BOOKING_SCRIPT),
        transport=transport,
        mode="text",
        channel="browser",
        clock=lambda: now,
    )
    await session.start(config)
    await transport.wait_for("agent.done")
    caller = SimulatedCaller(session, transport)
    for line in ("A cleaning tomorrow morning?", "Nine. Maria Lopez, 1988-03-14.", "Yes."):
        await caller.say(line)
    await session.hang_up()
    return session.call_id


def test_percentile() -> None:
    assert percentile([], 0.5) is None
    assert percentile([100, 200, 300, 400], 0.5) == 250
    assert percentile([100, 200, 300, 400], 0.9) == 370


def test_stats_ignore_overlapped_turns() -> None:
    events = [
        {"type": "latency", "payload": {"latency_ms": 900}},
        {"type": "latency", "payload": {"latency_ms": 5, "overlapped": True}},
        {"type": "barge_in", "payload": {}},
        {"type": "tool_call", "payload": {"guardrail": "confirmation_required"}},
        {"type": "tool_call", "payload": {"guardrail": "slot_not_offered"}},
    ]
    assert call_stats(events) == {
        "turns": 2,
        "latency_p50_ms": 900,
        "latency_p90_ms": 900,
        "barge_ins": 1,
        "tool_calls": 2,
        "guardrail_blocks": 1,
    }


async def test_list_and_get_a_recorded_call(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> None:
    call_id = await make_booking_call(conn, tz, now)

    [listed] = list_calls(conn)
    assert listed["id"] == call_id
    assert listed["outcome"] == "booked"
    assert listed["duration_s"] == 0.0  # the clock is frozen in tests
    assert listed["stats"]["turns"] == 3
    assert listed["stats"]["tool_calls"] == 4

    detail = get_call(conn, call_id)
    assert detail is not None
    types = {e["type"] for e in detail["events"]}
    assert {"transcript", "tool_call", "latency", "segment", "call_ended"} <= types
    assert get_call(conn, "call_missing") is None


async def test_transcript_text_for_summaries(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> None:
    call_id = await make_booking_call(conn, tz, now)
    detail = get_call(conn, call_id)
    assert detail is not None
    text = transcript_text(detail["events"])
    assert text.startswith("Receptionist: Thanks for calling Brightside Dental")
    assert "Caller: Yes." in text
    assert "[tool] book_appointment -> booked" in text
