"""A deterministic provider that replays a script instead of calling a model.

It exists so the whole pipeline (session, guardrails, tools, database, call
log) can be tested end to end in CI without API keys or network access. Each
caller message plays the next `Turn` of the script. Tool arguments can be
computed from earlier tool results, so a script can book whichever slot
`check_availability` actually returned.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from callpilot.providers.base import (
    AgentTextDelta,
    AgentTextDone,
    Mode,
    ProviderError,
    RealtimeProvider,
    ResponseDone,
    ResponseStarted,
    SessionConfig,
    ToolCallRequested,
)

Results = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class CallTool:
    name: str
    arguments: dict[str, Any] | Callable[[Results], dict[str, Any]]


@dataclass(frozen=True)
class Turn:
    actions: Sequence[Say | CallTool] = field(default_factory=tuple)


class ScriptedProvider(RealtimeProvider):
    name: ClassVar[str] = "scripted"
    modes: ClassVar[frozenset[Mode]] = frozenset({"text"})

    def __init__(self, turns: Sequence[Turn]) -> None:
        super().__init__(model="script")
        self._turns = list(turns)
        self._config: SessionConfig | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._calls = 0
        #: The latest result of each tool, available to argument callables.
        self.results: Results = {}

    async def connect(self, config: SessionConfig) -> None:
        self._config = config

    async def greet(self) -> None:
        assert self._config is not None, "connect() first"
        self._emit(ResponseStarted())
        self._say(self._config.greeting)
        self._emit(ResponseDone())

    async def send_text(self, text: str) -> None:
        if not self._turns:
            self._emit(ProviderError("script exhausted", fatal=True))
            return
        turn = self._turns.pop(0)
        self._task = asyncio.create_task(self._play(turn))

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        self.results[name] = result
        future = self._pending.pop(call_id, None)
        if future is not None and not future.done():
            future.set_result(result)

    async def interrupt(self, audio_played_ms: int | None) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._emit(ResponseDone(interrupted=True))

    async def wait_idle(self) -> None:
        """Wait until the current turn has finished playing (tests use this)."""
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _play(self, turn: Turn) -> None:
        self._emit(ResponseStarted())
        for action in turn.actions:
            if isinstance(action, Say):
                self._say(action.text)
                continue
            arguments = (
                action.arguments(self.results) if callable(action.arguments) else action.arguments
            )
            self._calls += 1
            call_id = f"scripted-{self._calls}"
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[call_id] = future
            self._emit(ToolCallRequested(call_id, action.name, arguments))
            await future
        self._emit(ResponseDone())

    def _say(self, text: str) -> None:
        # Stream word by word, like a real model would.
        words = text.split(" ")
        for index, word in enumerate(words):
            self._emit(AgentTextDelta(word if index == 0 else f" {word}"))
        self._emit(AgentTextDone(text))
