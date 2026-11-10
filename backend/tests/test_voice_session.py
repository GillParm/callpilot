"""Voice-mode behaviour of CallSession: barge-in, turn counting and latency."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from callpilot.providers.base import (
    AgentAudio,
    CallerSpeechStarted,
    CallerSpeechStopped,
    CallerTranscript,
    Mode,
    ProviderEvent,
    RealtimeProvider,
    ResponseDone,
    ResponseStarted,
    SessionConfig,
)
from callpilot.session.call_session import CallSession
from callpilot.session.factory import build_session
from tests.support import RecordingTransport

ONE_SECOND_OF_AUDIO = b"\x00\x00" * 24_000
LOUD_20MS = (0.3 * 32767 * np.sin(np.linspace(0, 8 * np.pi, 480))).astype("<i2").tobytes()
QUIET_20MS = b"\x00\x00" * 480


class FakeVoiceProvider(RealtimeProvider):
    """A provider the test drives by hand."""

    name: ClassVar[str] = "fake-voice"
    modes: ClassVar[frozenset[Mode]] = frozenset({"voice"})

    def __init__(self) -> None:
        super().__init__(model="fake")
        self.audio_in: list[bytes] = []
        self.interrupts: list[int | None] = []

    async def connect(self, config: SessionConfig) -> None:
        pass

    async def greet(self) -> None:
        pass

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_in.append(pcm)

    async def send_text(self, text: str) -> None:
        pass

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        pass

    async def interrupt(self, audio_played_ms: int | None) -> None:
        self.interrupts.append(audio_played_ms)

    def push(self, *events: ProviderEvent) -> None:
        for event in events:
            self._emit(event)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture
async def voice(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> tuple[CallSession, FakeVoiceProvider, RecordingTransport, Clock]:
    provider = FakeVoiceProvider()
    transport = RecordingTransport()
    session, config = build_session(
        conn=conn,
        tz=tz,
        provider=provider,
        transport=transport,
        mode="voice",
        channel="browser",
        clock=lambda: now,
    )
    clock = Clock()
    session._monotonic = clock
    await session.start(config)
    return session, provider, transport, clock


async def test_caller_audio_reaches_the_provider(voice: Any) -> None:
    session, provider, _, _ = voice
    await session.caller_audio(LOUD_20MS)
    assert provider.audio_in == [LOUD_20MS]


async def test_barge_in_while_agent_is_talking(voice: Any) -> None:
    session, provider, transport, _ = voice
    provider.push(ResponseStarted(), AgentAudio(ONE_SECOND_OF_AUDIO))
    await settle()
    session.playback_progress(400)  # the caller has heard 400 ms so far

    provider.push(CallerSpeechStarted())
    await settle()

    assert transport.clears == 1
    assert provider.interrupts == [400], "the provider is told what was actually heard"
    [event] = transport.of_type("barge_in")
    assert event == {"type": "barge_in", "trigger": "caller_speech", "audio_played_ms": 400}


async def test_barge_in_when_response_is_done_but_audio_still_playing(voice: Any) -> None:
    session, provider, transport, _ = voice
    provider.push(ResponseStarted(), AgentAudio(ONE_SECOND_OF_AUDIO), ResponseDone())
    await settle()
    session.playback_progress(300)  # most of that second is still queued in the browser

    provider.push(CallerSpeechStarted())
    await settle()
    assert transport.clears == 1


async def test_no_barge_in_once_the_caller_has_heard_everything(voice: Any) -> None:
    session, provider, transport, _ = voice
    provider.push(ResponseStarted(), AgentAudio(ONE_SECOND_OF_AUDIO), ResponseDone())
    await settle()
    session.playback_progress(1000)

    provider.push(CallerSpeechStarted())
    await settle()
    assert transport.clears == 0
    assert provider.interrupts == []


async def test_one_utterance_counts_as_one_caller_turn(voice: Any) -> None:
    session, provider, _, _ = voice
    provider.push(
        CallerSpeechStarted(),
        CallerSpeechStopped(),
        CallerTranscript("Hi there", opens_turn=False),
    )
    await settle()
    assert session.state.caller_turns == 1

    # Providers without speech events (Gemini) open the turn with the transcript.
    provider.push(ResponseStarted(), ResponseDone(), CallerTranscript("I need a cleaning"))
    await settle()
    assert session.state.caller_turns == 2


async def test_time_to_first_audio_is_measured_from_the_last_voiced_frame(voice: Any) -> None:
    session, provider, transport, clock = voice

    provider.push(CallerSpeechStarted())
    await settle()
    for _ in range(25):  # half a second of speech, 20 ms chunks
        clock.now += 0.02
        await session.caller_audio(LOUD_20MS)
    spoke_until = clock.now
    for _ in range(25):  # then silence
        clock.now += 0.02
        await session.caller_audio(QUIET_20MS)

    provider.push(CallerSpeechStopped())  # the provider's VAD notices 500 ms later
    await settle()
    clock.now = spoke_until + 0.8
    provider.push(ResponseStarted(), AgentAudio(b"\x00\x00" * 480))
    await settle()

    [metric] = transport.of_type("metrics.turn")
    assert metric["kind"] == "time_to_first_audio"
    assert metric["latency_ms"] == 800
    assert metric["endpointing_ms"] == 500
    assert metric["response_ms"] == 300
    assert metric["overlapped"] is False


async def test_overlapping_speech_is_flagged(voice: Any) -> None:
    session, provider, transport, clock = voice
    provider.push(CallerSpeechStarted())
    await settle()
    for _ in range(10):
        clock.now += 0.02
        await session.caller_audio(LOUD_20MS)
    # The agent starts answering while the caller is still mid-sentence.
    provider.push(ResponseStarted(), AgentAudio(b"\x00\x00" * 480))
    await settle()
    [metric] = transport.of_type("metrics.turn")
    assert metric["overlapped"] is True


async def test_client_reported_latency_is_recorded(voice: Any, conn: sqlite3.Connection) -> None:
    session, *_ = voice
    session.record_client_latency({"turn": 1, "perceived_ms": 912})
    session.record_client_latency({"turn": 2, "perceived_ms": "nonsense"})
    rows = conn.execute("SELECT payload FROM call_events WHERE type = 'client_latency'").fetchall()
    assert len(rows) == 1


async def test_speech_segments_are_recorded_for_the_call_log(
    voice: Any, conn: sqlite3.Connection
) -> None:
    session, provider, _, _ = voice
    provider.push(CallerSpeechStarted(), CallerSpeechStopped())
    provider.push(ResponseStarted(), AgentAudio(ONE_SECOND_OF_AUDIO), ResponseDone())
    await settle()
    # A second response the caller cuts off after 400 ms.
    provider.push(ResponseStarted(), AgentAudio(ONE_SECOND_OF_AUDIO))
    await settle()
    session.playback_progress(400)
    provider.push(CallerSpeechStarted())
    await settle()

    rows = conn.execute(
        "SELECT payload FROM call_events WHERE call_id = ? AND type = 'segment' ORDER BY id",
        (session.call_id,),
    ).fetchall()
    segments = [json.loads(r["payload"]) for r in rows]
    assert [s["lane"] for s in segments] == ["caller", "agent", "agent"]
    first_agent, cut_off = segments[1], segments[2]
    assert first_agent["end_ms"] - first_agent["start_ms"] == 1000
    assert cut_off["end_ms"] - cut_off["start_ms"] == 400
