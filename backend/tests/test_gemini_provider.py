"""GeminiLiveProvider's translation of Live API messages into provider events."""

from __future__ import annotations

from typing import Any

from google.genai import types

from callpilot.agent.prompt import GREETING
from callpilot.providers.base import (
    AgentAudio,
    AgentTextDelta,
    AgentTextDone,
    CallerSpeechStarted,
    CallerTranscript,
    ProviderEvent,
    ResponseDone,
    ResponseStarted,
    SessionConfig,
    ToolCallRequested,
)
from callpilot.providers.gemini_live import GeminiLiveProvider, build_live_config
from callpilot.tools.registry import AGENT_TOOLS

CONFIG = SessionConfig(instructions="be nice", tools=AGENT_TOOLS, mode="voice", greeting=GREETING)


def provider() -> GeminiLiveProvider:
    return GeminiLiveProvider(api_key="test")


def drain(p: GeminiLiveProvider) -> list[ProviderEvent]:
    events: list[ProviderEvent] = []
    while not p._events.empty():
        event = p._events.get_nowait()
        assert event is not None
        events.append(event)
    return events


def content(**kwargs: Any) -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(**kwargs))


def audio(data: bytes) -> types.LiveServerMessage:
    blob = types.Blob(data=data, mime_type="audio/pcm;rate=24000")
    return content(model_turn=types.Content(parts=[types.Part(inline_data=blob)]))


def heard(text: str) -> types.LiveServerMessage:
    return content(input_transcription=types.Transcription(text=text))


def said(text: str) -> types.LiveServerMessage:
    return content(output_transcription=types.Transcription(text=text))


def test_live_config_declares_tools_and_transcription() -> None:
    config = build_live_config(CONFIG, "Kore")
    assert config.response_modalities == [types.Modality.AUDIO]
    assert config.tools is not None
    declarations = config.tools[0].function_declarations or []
    assert {d.name for d in declarations} == {t.name for t in AGENT_TOOLS}
    assert all(d.behavior == types.Behavior.BLOCKING for d in declarations)
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None


def test_a_caller_turn_then_a_spoken_answer() -> None:
    p = provider()
    for message in (heard("I'd like "), heard("a cleaning"), audio(b"\x01\x02"), said("Sure!")):
        p.handle_message(message)
    p.handle_message(content(turn_complete=True))

    assert drain(p) == [
        CallerTranscript("I'd like a cleaning", opens_turn=True),
        ResponseStarted(),
        AgentAudio(b"\x01\x02"),
        AgentTextDelta("Sure!"),
        AgentTextDone("Sure!"),
        ResponseDone(interrupted=False),
    ]


def test_greeting_turn_does_not_open_a_caller_turn() -> None:
    p = provider()
    p._next_turn_is_ours = True  # what greet() sets
    p.handle_message(audio(b"\x00\x00"))
    events = drain(p)
    assert not any(isinstance(e, CallerTranscript) for e in events)
    assert events[0] == ResponseStarted()


def test_interruption_signals_barge_in_and_ends_the_response() -> None:
    p = provider()
    p.handle_message(audio(b"\x00\x00"))
    drain(p)
    p.handle_message(content(interrupted=True))
    assert drain(p) == [CallerSpeechStarted(), ResponseDone(interrupted=True)]

    # The caller's words then open the next response without counting the turn twice.
    p.handle_message(heard("actually, Thursday"))
    p.handle_message(audio(b"\x00\x00"))
    assert drain(p)[0] == CallerTranscript("actually, Thursday", opens_turn=False)


def test_tool_calls_are_forwarded_with_their_ids() -> None:
    p = provider()
    call = types.FunctionCall(id="fc-1", name="lookup_patient", args={"full_name": "Maria Lopez"})
    p.handle_message(
        types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[call]))
    )
    events = drain(p)
    assert events[-1] == ToolCallRequested("fc-1", "lookup_patient", {"full_name": "Maria Lopez"})


async def test_local_interrupt_drops_the_rest_of_the_response() -> None:
    p = provider()
    p.handle_message(audio(b"\x00\x00"))
    drain(p)
    await p.interrupt(audio_played_ms=120)
    assert drain(p) == [ResponseDone(interrupted=True)]
    p.handle_message(audio(b"\x09\x09"))  # still in flight from the cancelled turn
    assert drain(p) == []
    p.handle_message(content(turn_complete=True))
    p.handle_message(audio(b"\x05\x05"))  # the next turn plays normally
    assert AgentAudio(b"\x05\x05") in drain(p)
