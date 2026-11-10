"""Assemble a CallSession and everything it depends on for one call."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from callpilot.agent.guardrails import GuardedToolExecutor
from callpilot.agent.prompt import GREETING, build_instructions
from callpilot.agent.state import ConversationState
from callpilot.calllog.recorder import CallRecorder
from callpilot.clinic.repository import ClinicRepository
from callpilot.providers.base import Mode, RealtimeProvider, SessionConfig
from callpilot.session.call_session import CallSession, CallTransport
from callpilot.tools.clinic_tools import ClinicTools
from callpilot.tools.registry import AGENT_TOOLS


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def build_session(
    *,
    conn: sqlite3.Connection,
    tz: ZoneInfo,
    provider: RealtimeProvider,
    transport: CallTransport,
    mode: Mode,
    channel: str,
    clock: Callable[[], datetime],
    voice: str | None = None,
    caller_number: str | None = None,
    call_id: str | None = None,
) -> tuple[CallSession, SessionConfig]:
    call_id = call_id or new_call_id()
    local_clock: Callable[[], datetime] = lambda: clock().astimezone(tz)  # noqa: E731

    tools = ClinicTools(ClinicRepository(conn, tz), clock=local_clock, call_id=call_id)
    executor = GuardedToolExecutor(tools, ConversationState(call_id=call_id))
    recorder = CallRecorder(conn, call_id=call_id, clock=local_clock)
    recorder.start(
        channel=channel,
        mode=mode,
        provider=provider.name,
        model=provider.model,
        caller_number=caller_number,
    )
    session = CallSession(
        call_id=call_id,
        mode=mode,
        provider=provider,
        executor=executor,
        recorder=recorder,
        transport=transport,
    )
    config = SessionConfig(
        instructions=build_instructions(local_clock(), mode),
        tools=AGENT_TOOLS,
        mode=mode,
        greeting=GREETING,
        voice=voice,
    )
    return session, config
