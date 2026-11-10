"""A full booking call, end to end, in text mode.

The caller's lines are scripted, and so is the agent (ScriptedProvider), but
everything in between is real: the CallSession, guardrails, clinic tools,
SQLite and the call log. This is the test that proves the plumbing works; the
`live` variant in test_live_conversation.py swaps in a real model.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from callpilot.providers.scripted import CallTool, Results, Say, ScriptedProvider, Turn, Wait
from callpilot.session.call_session import CallSession
from callpilot.session.factory import build_session
from tests.support import RecordingTransport


class SimulatedCaller:
    def __init__(self, session: CallSession, transport: RecordingTransport) -> None:
        self.session = session
        self.transport = transport

    async def say(self, text: str) -> None:
        done = len(self.transport.of_type("agent.done"))
        await self.session.caller_text(text)
        await self.transport.wait_for("agent.done", done + 1)


def first_slot(results: Results) -> str:
    return str(results["check_availability"]["slots"][0]["slot_id"])


def booking(confirmed: bool) -> Callable[[Results], dict[str, object]]:
    def build(results: Results) -> dict[str, object]:
        return {
            "patient_id": results["lookup_patient"]["patient_id"],
            "slot_id": first_slot(results),
            "reason": "cleaning",
            "caller_confirmed": confirmed,
        }

    return build


BOOKING_SCRIPT = [
    Turn(
        [
            Say("Happy to help. Let me check tomorrow morning."),
            CallTool(
                "check_availability",
                {
                    "appointment_type": "cleaning",
                    "date_from": "2026-11-11",
                    "time_of_day": "morning",
                },
            ),
            Say("I have nine o'clock or ten o'clock with Priya. Which suits you?"),
        ]
    ),
    Turn(
        [
            CallTool("lookup_patient", {"full_name": "Maria Lopez", "date_of_birth": "1988-03-14"}),
            CallTool("book_appointment", booking(confirmed=False)),
            Say("That's a cleaning for Maria Lopez tomorrow at nine with Priya. Shall I book it?"),
        ]
    ),
    Turn(
        [
            CallTool("book_appointment", booking(confirmed=True)),
            Say("You're all set for tomorrow at nine. Anything else?"),
        ]
    ),
    Turn([Say("Thanks for calling Brightside Dental. Goodbye!")]),
]


@pytest.fixture
async def call(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> tuple[CallSession, RecordingTransport, ScriptedProvider]:
    provider = ScriptedProvider(BOOKING_SCRIPT)
    transport = RecordingTransport()
    session, config = build_session(
        conn=conn,
        tz=tz,
        provider=provider,
        transport=transport,
        mode="text",
        channel="browser",
        clock=lambda: now,
    )
    await session.start(config)
    await transport.wait_for("agent.done")
    return session, transport, provider


async def test_full_booking_conversation(
    call: tuple[CallSession, RecordingTransport, ScriptedProvider], conn: sqlite3.Connection
) -> None:
    session, transport, _ = call
    caller = SimulatedCaller(session, transport)

    await caller.say("Hi, can I book a cleaning tomorrow morning?")
    await caller.say("The nine o'clock please. It's Maria Lopez, born March 14th 1988.")
    await caller.say("Yes, that's right.")
    await caller.say("No, that's all. Thanks!")
    await session.hang_up()

    # The caller heard a greeting, the options, a read-back, a confirmation and a goodbye.
    lines = transport.agent_lines()
    assert lines[0].startswith("Thanks for calling Brightside Dental")
    assert "Shall I book it?" in lines[-3]
    assert lines[-1].endswith("Goodbye!")

    # The booking really happened, exactly once, for the right patient.
    rows = conn.execute(
        "SELECT a.reason, a.source_call_id FROM appointments a "
        "WHERE a.patient_id = 'P-1001' AND a.status = 'booked' AND a.reason = 'cleaning' "
        "AND a.source_call_id IS NOT NULL"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["source_call_id"] == session.call_id

    # Tool activity was streamed to the UI, including the confirmation step.
    results = transport.of_type("tool.result")
    assert [r["name"] for r in results] == [
        "check_availability",
        "lookup_patient",
        "book_appointment",
        "book_appointment",
    ]
    assert results[2]["guardrail"] == "confirmation_required"
    assert results[3]["result"]["status"] == "booked"

    # Every caller turn produced a latency measurement.
    metrics = transport.of_type("metrics.turn")
    assert [m["turn"] for m in metrics] == [1, 2, 3, 4]
    assert all(m["kind"] == "time_to_first_token" for m in metrics)

    assert transport.of_type("call.ended")[0]["outcome"] == "booked"


async def test_call_log_captures_the_whole_call(
    call: tuple[CallSession, RecordingTransport, ScriptedProvider], conn: sqlite3.Connection
) -> None:
    session, transport, _ = call
    caller = SimulatedCaller(session, transport)
    await caller.say("Hi, can I book a cleaning tomorrow morning?")
    await session.hang_up()

    row = conn.execute("SELECT * FROM calls WHERE id = ?", (session.call_id,)).fetchone()
    assert row["mode"] == "text"
    assert row["provider"] == "scripted"
    assert row["ended_at"] is not None
    assert row["outcome"] == "info"

    events = conn.execute(
        "SELECT type, payload FROM call_events WHERE call_id = ? ORDER BY id", (session.call_id,)
    ).fetchall()
    types = [e["type"] for e in events]
    assert types[0] == "transcript"  # the greeting
    assert {"transcript", "tool_call", "latency", "call_ended"} <= set(types)
    tool = json.loads(next(e["payload"] for e in events if e["type"] == "tool_call"))
    assert tool["name"] == "check_availability"
    assert tool["ok"] is True


async def test_caller_talking_over_the_agent_cancels_the_response(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> None:
    never = asyncio.Event()
    provider = ScriptedProvider(
        [
            Turn([Say("Let me read you all of our opening hours"), Wait(never), Say("unsaid")]),
            Turn([Say("Of course, I'll get someone for you.")]),
        ]
    )
    transport = RecordingTransport()
    session, config = build_session(
        conn=conn, tz=tz, provider=provider, transport=transport,
        mode="text", channel="browser", clock=lambda: now,
    )  # fmt: skip
    await session.start(config)
    await transport.wait_for("agent.done")

    await session.caller_text("What are your hours?")
    await transport.wait_for("transcript", 3)  # greeting, caller, first agent line
    await session.caller_text("Sorry, can I just talk to a person?")
    await transport.wait_for("agent.done", 3)

    [barge_in] = transport.of_type("barge_in")
    assert barge_in["trigger"] == "caller_text"
    assert transport.of_type("agent.done")[1]["interrupted"] is True
    assert "unsaid" not in " ".join(transport.agent_lines())
    assert transport.agent_lines()[-1] == "Of course, I'll get someone for you."
    await session.hang_up()
