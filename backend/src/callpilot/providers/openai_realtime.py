"""OpenAI Realtime API (GA) over a server-to-server WebSocket.

Voice mode streams PCM16 at 24 kHz both ways with server VAD; text mode uses
the same session with text output. Event mapping:

* `input_audio_buffer.speech_started` -> `CallerSpeechStarted` (barge-in). With
  `interrupt_response` on, the server also cancels the response itself; we
  still send `conversation.item.truncate` with what the caller actually heard,
  so the model's memory matches the conversation.
* A response that ends in function calls is followed by a continuation
  response once all results are in. The two are reported as one response so
  every provider looks the same to the session.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Protocol

import websockets

from callpilot.providers.base import (
    SAMPLE_RATE,
    AgentAudio,
    AgentTextDelta,
    AgentTextDone,
    CallerSpeechStarted,
    CallerSpeechStopped,
    CallerTranscript,
    Mode,
    ProviderError,
    RealtimeProvider,
    ResponseDone,
    ResponseStarted,
    SessionConfig,
    ToolCallRequested,
)
from callpilot.tools.registry import openai_tools

log = logging.getLogger(__name__)

REALTIME_URL = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime-2.1"
DEFAULT_VOICE = "marin"
TRANSCRIPTION_MODEL = "gpt-transcribe"
# Errors that only mean "there was nothing to cancel".
BENIGN_ERRORS = {"response_cancel_not_active"}


class Socket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


Connector = Callable[[str, dict[str, str]], Awaitable[Socket]]


async def _default_connect(url: str, headers: dict[str, str]) -> Socket:
    socket: Socket = await websockets.connect(url, additional_headers=headers, max_size=None)
    return socket


def session_payload(config: SessionConfig, *, model: str, voice: str) -> dict[str, Any]:
    session: dict[str, Any] = {
        "type": "realtime",
        "model": model,
        "instructions": config.instructions,
        "tools": openai_tools(config.tools),
        "tool_choice": "auto",
    }
    if config.mode == "text":
        session["output_modalities"] = ["text"]
        return session
    session["output_modalities"] = ["audio"]
    session["audio"] = {
        "input": {
            "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
            "transcription": {"model": TRANSCRIPTION_MODEL},
            "noise_reduction": {"type": "near_field"},
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
                "create_response": True,
                "interrupt_response": True,
            },
        },
        "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "voice": voice},
    }
    return session


class OpenAIRealtimeProvider(RealtimeProvider):
    name: ClassVar[str] = "openai"
    modes: ClassVar[frozenset[Mode]] = frozenset({"voice", "text"})

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        connector: Connector = _default_connect,
    ) -> None:
        super().__init__(model=model)
        self._api_key = api_key
        self._voice = voice
        self._connector = connector
        self._config: SessionConfig | None = None
        self._socket: Socket | None = None
        self._receiver: asyncio.Task[None] | None = None

        self._response_active = False
        self._continuing = False  # next response continues after tool calls
        self._calls_in_response: list[str] = []
        self._outstanding_calls: set[str] = set()
        self._audio_item_id: str | None = None

    # Lifecycle -------------------------------------------------------------------

    async def connect(self, config: SessionConfig) -> None:
        self._config = config
        self._socket = await self._connector(
            f"{REALTIME_URL}?model={self.model}",
            {"Authorization": f"Bearer {self._api_key}"},
        )
        payload = session_payload(config, model=self.model, voice=config.voice or self._voice)
        await self._send({"type": "session.update", "session": payload})
        self._receiver = asyncio.create_task(self._receive_loop(), name="openai-receive")

    async def close(self) -> None:
        if self._receiver is not None:
            self._receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._receiver
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.close()
        await super().close()

    # Input -----------------------------------------------------------------------

    async def greet(self) -> None:
        assert self._config is not None, "connect() first"
        greeting = self._config.greeting
        if self._config.mode == "text":
            # No need to spend a model turn on fixed text: record it and show it.
            await self._send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": greeting}],
                    },
                }
            )
            for event in (
                ResponseStarted(),
                AgentTextDelta(greeting),
                AgentTextDone(greeting),
                ResponseDone(),
            ):
                self._emit(event)
            return
        await self._send(
            {
                "type": "response.create",
                "response": {"instructions": f'Greet the caller by saying exactly: "{greeting}"'},
            }
        )

    async def send_audio(self, pcm: bytes) -> None:
        await self._send(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}
        )

    async def send_text(self, text: str) -> None:
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self._send({"type": "response.create"})

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result),
                },
            }
        )
        self._outstanding_calls.discard(call_id)
        await self._continue_if_ready()

    async def interrupt(self, audio_played_ms: int | None) -> None:
        if self._response_active:
            await self._send({"type": "response.cancel"})
        if self._audio_item_id is not None and audio_played_ms is not None:
            await self._send(
                {
                    "type": "conversation.item.truncate",
                    "item_id": self._audio_item_id,
                    "content_index": 0,
                    "audio_end_ms": audio_played_ms,
                }
            )
        self._continuing = False
        self._outstanding_calls.clear()

    # Output ----------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        assert self._socket is not None
        try:
            while True:
                raw = await self._socket.recv()
                await self.handle_server_event(json.loads(raw))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("openai realtime session ended: %s", exc)
            self._emit(ProviderError("The voice connection was lost.", fatal=True))

    async def handle_server_event(self, event: dict[str, Any]) -> None:
        """Translate one server event into provider events (public for tests)."""
        match event.get("type"):
            case "input_audio_buffer.speech_started":
                self._emit(CallerSpeechStarted())
            case "input_audio_buffer.speech_stopped":
                self._emit(CallerSpeechStopped())
            case "conversation.item.input_audio_transcription.completed":
                text = str(event.get("transcript", "")).strip()
                if text:
                    # speech_started already opened the caller's turn.
                    self._emit(CallerTranscript(text, opens_turn=False))
            case "response.created":
                self._response_active = True
                self._calls_in_response = []
                if self._continuing:
                    self._continuing = False
                else:
                    self._emit(ResponseStarted())
            case "response.output_audio.delta":
                self._audio_item_id = event.get("item_id", self._audio_item_id)
                self._emit(AgentAudio(base64.b64decode(event["delta"])))
            case "response.output_audio_transcript.delta" | "response.output_text.delta":
                self._emit(AgentTextDelta(event.get("delta", "")))
            case "response.output_audio_transcript.done":
                self._emit(AgentTextDone(event.get("transcript", "")))
            case "response.output_text.done":
                self._emit(AgentTextDone(event.get("text", "")))
            case "response.function_call_arguments.done":
                call_id = event["call_id"]
                self._calls_in_response.append(call_id)
                self._outstanding_calls.add(call_id)
                self._emit(ToolCallRequested(call_id, event["name"], event.get("arguments", "{}")))
            case "response.done":
                await self._response_done(event.get("response", {}))
            case "error":
                error = event.get("error", {})
                if error.get("code") not in BENIGN_ERRORS:
                    log.warning("openai realtime error: %s", error)
                    self._emit(ProviderError(str(error.get("message", "Unknown error"))))

    async def _response_done(self, response: dict[str, Any]) -> None:
        self._response_active = False
        status = response.get("status")
        if status == "completed" and self._calls_in_response:
            # Tool calls: the answer continues once their results are in.
            self._continuing = True
            await self._continue_if_ready()
            return
        self._emit(ResponseDone(interrupted=status == "cancelled"))

    async def _continue_if_ready(self) -> None:
        if self._continuing and not self._outstanding_calls and not self._response_active:
            await self._send({"type": "response.create"})

    async def _send(self, message: dict[str, Any]) -> None:
        if self._socket is None:
            raise RuntimeError("not connected")
        await self._socket.send(json.dumps(message))
