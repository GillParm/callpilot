"""The simulated-caller test against a real model.

Same pipeline as test_simulated_caller.py, but the agent is the configured
text provider and the caller is another model playing a patient. It checks
outcomes, not wording: the booking must exist, it must have gone through the
two-step confirmation, and no guardrail may have caught invented availability
or a booking for the wrong patient.

Run with:  uv run pytest -m live -s
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import anthropic
import pytest

from callpilot.config import Settings
from callpilot.db import connect, init_db
from callpilot.db.seed import seed_all
from callpilot.providers.factory import create_provider, resolve_provider_name
from callpilot.session.factory import build_session
from tests.support import RecordingTransport

pytestmark = pytest.mark.live

MAX_TURNS = 12
END_TOKEN = "[END]"

CALLER_PERSONA = f"""\
You are role-playing a patient phoning a dental clinic. Stay in character.
You are Maria Lopez, date of birth March 14, 1988. You want a teeth cleaning
some morning in the next week; any morning is fine. Speak like a real caller:
short, natural sentences, and only answer what you're asked. Give your name and
date of birth when asked. When the receptionist reads the booking back, say yes
if it is a cleaning for Maria Lopez on a morning. Once they confirm it's booked,
thank them and say goodbye, then write {END_TOKEN} on its own at the end.
Output only what Maria says out loud."""


@pytest.fixture
def settings() -> Settings:
    settings = Settings()
    if resolve_provider_name(settings, "text") is None or settings.anthropic_api_key is None:
        pytest.skip("needs ANTHROPIC_API_KEY (the caller is played by Claude)")
    return settings


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = connect(":memory:")
    init_db(conn)
    return conn


class ModelCaller:
    """A patient played by Claude. The agent's lines are its 'user' turns."""

    def __init__(self, settings: Settings) -> None:
        assert settings.anthropic_api_key is not None
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        self._model = settings.anthropic_model
        self._history: list[dict[str, Any]] = []

    async def reply_to(self, agent_said: str) -> str:
        self._history.append({"role": "user", "content": agent_said})
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=CALLER_PERSONA,
            output_config={"effort": "low"},
            messages=self._history,  # type: ignore[arg-type]
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        self._history.append({"role": "assistant", "content": text})
        return text


async def test_model_caller_books_a_cleaning(settings: Settings, db: sqlite3.Connection) -> None:
    tz = ZoneInfo(settings.clinic_timezone)
    now = datetime.now(UTC)
    seed_all(db, now=now, tz=tz)

    transport = RecordingTransport()
    session, config = build_session(
        conn=db,
        tz=tz,
        provider=create_provider(settings, "text"),
        transport=transport,
        mode="text",
        channel="browser",
        clock=lambda: datetime.now(UTC),
    )
    caller = ModelCaller(settings)
    await session.start(config)
    await transport.wait_for("agent.done", within=60)

    transcript: list[str] = []
    seen_agent_lines = 0
    for turn in range(1, MAX_TURNS + 1):
        agent_lines = transport.agent_lines()[seen_agent_lines:]
        seen_agent_lines += len(agent_lines)
        agent_said = " ".join(agent_lines)
        transcript.append(f"AGENT:  {agent_said}")

        caller_said = await caller.reply_to(agent_said)
        finished = END_TOKEN in caller_said
        caller_said = caller_said.replace(END_TOKEN, "").strip()
        transcript.append(f"CALLER: {caller_said}")
        if finished:
            break
        await session.caller_text(caller_said)
        await transport.wait_for("agent.done", count=turn + 1, within=90)
    await session.hang_up()
    print("\n" + "\n".join(transcript))

    tool_results = transport.of_type("tool.result")
    print(json.dumps([(r["name"], r["guardrail"], r["ok"]) for r in tool_results]))

    booked = db.execute(
        "SELECT a.reason, s.starts_at FROM appointments a JOIN slots s ON s.id = a.slot_id "
        "WHERE a.patient_id = 'P-1001' AND a.source_call_id = ?",
        (session.call_id,),
    ).fetchall()
    assert len(booked) == 1, "exactly one appointment should have been booked"

    guardrails = [r["guardrail"] for r in tool_results if r["guardrail"]]
    assert "confirmation_required" in guardrails, "booking must go through the read-back"
    assert "slot_not_offered" not in guardrails, "the agent tried to book an invented slot"
    assert "patient_mismatch" not in guardrails
    names = [r["name"] for r in tool_results]
    assert names.index("check_availability") < names.index("book_appointment")
