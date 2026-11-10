"""The interface every model provider implements.

A provider turns a model's streaming API into a small set of events that the
`CallSession` understands, and accepts caller input (audio or text), tool
results and interruptions. Speech-to-speech providers (OpenAI Realtime, Gemini
Live) and text-only ones (Claude) look the same from the session's side, which
is what lets the same agent run in voice and text mode.

Audio crossing this boundary is always PCM16, mono, 24 kHz.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from callpilot.tools.registry import ToolSpec

Mode = Literal["voice", "text"]
SAMPLE_RATE = 24_000


@dataclass(frozen=True)
class SessionConfig:
    instructions: str
    tools: tuple[ToolSpec, ...]
    mode: Mode
    greeting: str
    voice: str | None = None


# Events ------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseStarted:
    """The model started a new response (speech, text and/or tool calls)."""


@dataclass(frozen=True)
class AgentAudio:
    pcm: bytes


@dataclass(frozen=True)
class AgentTextDelta:
    """A piece of what the agent is saying (its transcript in voice mode)."""

    text: str


@dataclass(frozen=True)
class AgentTextDone:
    """The full text of one agent utterance."""

    text: str


@dataclass(frozen=True)
class CallerSpeechStarted:
    """Voice activity detected on the caller's side: the barge-in signal."""


@dataclass(frozen=True)
class CallerSpeechStopped:
    pass


@dataclass(frozen=True)
class CallerTranscript:
    """What the caller said, transcribed by the provider (voice mode)."""

    text: str


@dataclass(frozen=True)
class ToolCallRequested:
    call_id: str
    name: str
    arguments: dict[str, Any] | str


@dataclass(frozen=True)
class ResponseDone:
    interrupted: bool = False


@dataclass(frozen=True)
class ProviderError:
    message: str
    fatal: bool = False


ProviderEvent = (
    ResponseStarted
    | AgentAudio
    | AgentTextDelta
    | AgentTextDone
    | CallerSpeechStarted
    | CallerSpeechStopped
    | CallerTranscript
    | ToolCallRequested
    | ResponseDone
    | ProviderError
)


class ProviderUnavailableError(Exception):
    """The provider can't be used (missing key, unsupported mode...)."""


# Base class ------------------------------------------------------------------------


class RealtimeProvider(ABC):
    """Base class with an event queue; subclasses call `_emit`."""

    name: ClassVar[str]
    modes: ClassVar[frozenset[Mode]]

    def __init__(self, model: str) -> None:
        self.model = model
        self._events: asyncio.Queue[ProviderEvent | None] = asyncio.Queue()
        self._closed = False

    @abstractmethod
    async def connect(self, config: SessionConfig) -> None: ...

    @abstractmethod
    async def greet(self) -> None:
        """Open the call with the configured greeting."""

    async def send_audio(self, pcm: bytes) -> None:
        raise ProviderUnavailableError(f"{self.name} does not accept audio")

    @abstractmethod
    async def send_text(self, text: str) -> None:
        """A complete caller utterance in text form; triggers a response."""

    @abstractmethod
    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None: ...

    @abstractmethod
    async def interrupt(self, audio_played_ms: int | None) -> None:
        """Cancel the in-flight response. `audio_played_ms` is how much of its
        audio the caller actually heard, so the model's memory can be trimmed."""

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._events.put_nowait(None)

    async def events(self) -> AsyncIterator[ProviderEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    def _emit(self, event: ProviderEvent) -> None:
        if not self._closed:
            self._events.put_nowait(event)
