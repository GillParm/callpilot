"""Claude as the brain for text-mode calls.

Claude has no speech-to-speech API, so this provider handles text mode only:
the same receptionist, tools and guardrails, typed instead of spoken. It runs
a standard streaming tool-use loop over the Messages API and maps it onto the
provider events the session expects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, ClassVar, Literal

import anthropic

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
from callpilot.tools.registry import anthropic_tools

log = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Opt into server-side refusal fallbacks: if the model declines a request the
# API re-runs it on a recommended fallback model in the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16_000
MAX_TOOL_ROUNDS = 6
REFUSAL_REPLY = (
    "Sorry, I can't help with that one. Is there anything about an appointment I can do?"
)


class AnthropicTextProvider(RealtimeProvider):
    name: ClassVar[str] = "anthropic"
    modes: ClassVar[frozenset[Mode]] = frozenset({"text"})

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        effort: Effort = "low",
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        super().__init__(model=model)
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)
        self._effort = effort
        self._config: SessionConfig | None = None
        self._history: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def connect(self, config: SessionConfig) -> None:
        if config.mode != "text":
            raise ValueError("AnthropicTextProvider only supports text mode")
        self._config = config
        self._tools = [{**t, "eager_input_streaming": True} for t in anthropic_tools(config.tools)]

    async def greet(self) -> None:
        # The greeting is fixed, so there's no need to spend a model call (and
        # a second of latency) on it. The history records that it was said.
        assert self._config is not None, "connect() first"
        greeting = self._config.greeting
        self._history = [
            {"role": "user", "content": "(The phone rings and you answer.)"},
            {"role": "assistant", "content": greeting},
        ]
        self._emit(ResponseStarted())
        self._emit(AgentTextDelta(greeting))
        self._emit(AgentTextDone(greeting))
        self._emit(ResponseDone())

    async def send_text(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})
        self._task = asyncio.create_task(self._respond())

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        future = self._pending.get(call_id)
        if future is not None and not future.done():
            future.set_result(result)

    async def interrupt(self, audio_played_ms: int | None) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._emit(ResponseDone(interrupted=True))

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        await super().close()

    # The tool-use loop ---------------------------------------------------------

    async def _respond(self) -> None:
        self._emit(ResponseStarted())
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                if not await self._one_round():
                    break
            self._emit(ResponseDone())
        except asyncio.CancelledError:
            self._close_open_tool_calls("The caller interrupted before this finished.")
            raise
        except anthropic.APIError as exc:
            log.warning("anthropic request failed: %s", exc)
            self._emit(ProviderError("The assistant is having trouble right now."))
            self._emit(ResponseDone())

    async def _one_round(self) -> bool:
        """Stream one model response. Returns True if tools ran and the loop continues."""
        assert self._config is not None
        text: list[str] = []
        async with self._client.beta.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=self._config.instructions,
            tools=self._tools,  # type: ignore[arg-type]
            messages=self._history,  # type: ignore[arg-type]
            output_config={"effort": self._effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    text.append(event.text)
                    self._emit(AgentTextDelta(event.text))
            message = await stream.get_final_message()

        if message.stop_reason == "refusal":
            # Discard any partial output and answer with a safe line instead.
            self._emit(AgentTextDone(REFUSAL_REPLY))
            self._history.append({"role": "assistant", "content": REFUSAL_REPLY})
            return False

        self._history.append({"role": "assistant", "content": message.content})
        if text:
            self._emit(AgentTextDone("".join(text)))

        tool_uses = [block for block in message.content if block.type == "tool_use"]
        if not tool_uses:
            return False
        if message.stop_reason == "max_tokens":
            self._append_tool_errors([b.id for b in tool_uses], "Output was cut off; not run.")
            return False

        loop = asyncio.get_running_loop()
        for block in tool_uses:
            self._pending[block.id] = loop.create_future()
            arguments = block.input if isinstance(block.input, dict) else {}
            self._emit(ToolCallRequested(block.id, block.name, arguments))
        results = [await self._pending[block.id] for block in tool_uses]
        self._history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                        "is_error": not result.get("ok", False),
                    }
                    for block, result in zip(tool_uses, results, strict=True)
                ],
            }
        )
        self._pending.clear()
        return True

    def _close_open_tool_calls(self, reason: str) -> None:
        if self._pending:
            self._append_tool_errors(list(self._pending), reason)
            self._pending.clear()

    def _append_tool_errors(self, tool_use_ids: list[str], reason: str) -> None:
        """Keep the history valid: every tool_use needs a matching tool_result."""
        self._history.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": json.dumps({"ok": False, "error": reason}),
                        "is_error": True,
                    }
                    for tool_use_id in tool_use_ids
                ],
            }
        )
