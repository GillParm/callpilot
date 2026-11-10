"""OpenAIRealtimeProvider against a fake WebSocket (no network)."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from callpilot.agent.prompt import GREETING
from callpilot.providers.base import (
    AgentAudio,
    AgentTextDone,
    CallerSpeechStarted,
    CallerTranscript,
    ProviderEvent,
    ResponseDone,
    ResponseStarted,
    SessionConfig,
    ToolCallRequested,
)
from callpilot.providers.openai_realtime import OpenAIRealtimeProvider, session_payload
from callpilot.tools.registry import AGENT_TOOLS


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        pass

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


def config(mode: str) -> SessionConfig:
    return SessionConfig(instructions="be nice", tools=AGENT_TOOLS, mode=mode, greeting=GREETING)  # type: ignore[arg-type]


@pytest.fixture
async def connected() -> tuple[OpenAIRealtimeProvider, FakeSocket]:
    socket = FakeSocket()

    async def connector(url: str, headers: dict[str, str]) -> FakeSocket:
        assert url.endswith("?model=gpt-realtime-2.1")
        assert headers == {"Authorization": "Bearer sk-test"}
        return socket

    provider = OpenAIRealtimeProvider(api_key="sk-test", connector=connector)
    await provider.connect(config("voice"))
    yield provider, socket
    await provider.close()


def drain(provider: OpenAIRealtimeProvider) -> list[ProviderEvent]:
    events = []
    while not provider._events.empty():
        event = provider._events.get_nowait()
        assert event is not None
        events.append(event)
    return events


def test_voice_session_payload() -> None:
    payload = session_payload(config("voice"), model="gpt-realtime-2.1", voice="marin")
    assert payload["type"] == "realtime"
    assert payload["output_modalities"] == ["audio"]
    assert payload["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    vad = payload["audio"]["input"]["turn_detection"]
    assert vad["type"] == "server_vad"
    assert vad["interrupt_response"] is True
    assert payload["audio"]["output"]["voice"] == "marin"
    assert {t["name"] for t in payload["tools"]} == {t.name for t in AGENT_TOOLS}


def test_text_session_payload_has_no_audio() -> None:
    payload = session_payload(config("text"), model="m", voice="v")
    assert payload["output_modalities"] == ["text"]
    assert "audio" not in payload


async def test_connect_configures_the_session(connected: Any) -> None:
    _, socket = connected
    assert socket.sent[0]["type"] == "session.update"


async def test_audio_is_base64_appended(connected: Any) -> None:
    provider, socket = connected
    await provider.send_audio(b"\x01\x02")
    assert socket.sent[-1] == {"type": "input_audio_buffer.append", "audio": "AQI="}


async def test_spoken_response_events(connected: Any) -> None:
    provider, _ = connected
    for event in (
        {"type": "input_audio_buffer.speech_started", "audio_start_ms": 0, "item_id": "u1"},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "Hi"},
        {"type": "response.created", "response": {"id": "r1"}},
        {
            "type": "response.output_audio.delta",
            "item_id": "a1",
            "delta": base64.b64encode(b"\x07\x07").decode(),
        },
        {"type": "response.output_audio_transcript.done", "transcript": "Hello!"},
        {"type": "response.done", "response": {"status": "completed"}},
    ):
        await provider.handle_server_event(event)
    assert drain(provider) == [
        CallerSpeechStarted(),
        CallerTranscript("Hi", opens_turn=False),
        ResponseStarted(),
        AgentAudio(b"\x07\x07"),
        AgentTextDone("Hello!"),
        ResponseDone(interrupted=False),
    ]


async def test_tool_calls_continue_as_one_response(connected: Any) -> None:
    provider, socket = connected
    await provider.handle_server_event({"type": "response.created", "response": {"id": "r1"}})
    await provider.handle_server_event(
        {"type": "response.function_call_arguments.done", "call_id": "c1",
         "name": "lookup_patient", "arguments": '{"full_name": "Maria Lopez"}'}
    )  # fmt: skip
    # The session answers the tool before response.done arrives.
    await provider.send_tool_result("c1", "lookup_patient", {"ok": True})
    assert "response.create" not in socket.types()
    await provider.handle_server_event(
        {"type": "response.done", "response": {"status": "completed"}}
    )
    assert socket.types()[-2:] == ["conversation.item.create", "response.create"]

    # The continuation is not reported as a new response.
    await provider.handle_server_event({"type": "response.created", "response": {"id": "r2"}})
    await provider.handle_server_event(
        {"type": "response.done", "response": {"status": "completed"}}
    )
    events = drain(provider)
    assert events.count(ResponseStarted()) == 1
    assert events.count(ResponseDone()) == 1
    assert ToolCallRequested("c1", "lookup_patient", '{"full_name": "Maria Lopez"}') in events


async def test_barge_in_cancels_and_truncates_to_what_was_heard(connected: Any) -> None:
    provider, socket = connected
    await provider.handle_server_event({"type": "response.created", "response": {"id": "r1"}})
    await provider.handle_server_event(
        {"type": "response.output_audio.delta", "item_id": "item_9", "delta": "AAA="}
    )
    await provider.interrupt(audio_played_ms=640)
    assert socket.sent[-2] == {"type": "response.cancel"}
    assert socket.sent[-1] == {
        "type": "conversation.item.truncate",
        "item_id": "item_9",
        "content_index": 0,
        "audio_end_ms": 640,
    }
    await provider.handle_server_event(
        {"type": "response.done", "response": {"status": "cancelled"}}
    )
    assert drain(provider)[-1] == ResponseDone(interrupted=True)


async def test_benign_cancel_errors_are_ignored(connected: Any) -> None:
    provider, _ = connected
    await provider.handle_server_event(
        {"type": "error", "error": {"code": "response_cancel_not_active", "message": "nope"}}
    )
    await provider.handle_server_event({"type": "error", "error": {"code": "x", "message": "boom"}})
    events = drain(provider)
    assert len(events) == 1
    assert events[0].message == "boom"  # type: ignore[union-attr]


async def test_text_mode_greeting_is_local(connected: Any) -> None:
    socket = FakeSocket()

    async def connector(url: str, headers: dict[str, str]) -> FakeSocket:
        return socket

    provider = OpenAIRealtimeProvider(api_key="sk", connector=connector)
    await provider.connect(config("text"))
    await provider.greet()
    assert socket.sent[-1]["item"]["role"] == "assistant"
    assert AgentTextDone(GREETING) in drain(provider)
    await provider.close()
