"""AnthropicTextProvider against a fake client (no network)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from callpilot.agent.prompt import GREETING
from callpilot.providers.anthropic_text import REFUSAL_REPLY, AnthropicTextProvider
from callpilot.providers.base import (
    AgentTextDone,
    ProviderEvent,
    ResponseDone,
    SessionConfig,
    ToolCallRequested,
)
from callpilot.tools.registry import AGENT_TOOLS


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_block(block_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=arguments)


class FakeStream:
    def __init__(self, deltas: list[str], message: SimpleNamespace) -> None:
        self._deltas = deltas
        self._message = message

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        for delta in self._deltas:
            yield SimpleNamespace(type="text", text=delta)

    async def get_final_message(self) -> SimpleNamespace:
        return self._message


class FakeClient:
    """Plays back canned responses and records every request."""

    def __init__(self, responses: list[tuple[list[str], SimpleNamespace]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs: Any) -> FakeStream:
        # Snapshot the history: the provider keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        deltas, message = self._responses.pop(0)
        return FakeStream(deltas, message)


def message(stop_reason: str, *blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


CONFIG = SessionConfig(instructions="be nice", tools=AGENT_TOOLS, mode="text", greeting=GREETING)


async def collect_until_done(provider: AnthropicTextProvider) -> list[ProviderEvent]:
    events: list[ProviderEvent] = []
    async for event in provider.events():
        events.append(event)
        if isinstance(event, ResponseDone):
            return events
        if isinstance(event, ToolCallRequested):
            await provider.send_tool_result(event.call_id, event.name, {"ok": True, "slots": []})
    return events


@pytest.fixture
async def provider_factory() -> Any:
    async def make(client: FakeClient) -> AnthropicTextProvider:
        provider = AnthropicTextProvider(api_key="x", model="claude-opus-5-5", client=client)
        await provider.connect(CONFIG)
        await provider.greet()
        await collect_until_done(provider)
        return provider

    return make


async def test_greeting_is_instant_and_recorded_in_history(provider_factory: Any) -> None:
    client = FakeClient([])
    provider = await provider_factory(client)
    assert client.requests == []
    assert provider._history[-1] == {"role": "assistant", "content": GREETING}


async def test_tool_round_trip_then_answer(provider_factory: Any) -> None:
    client = FakeClient(
        [
            ([], message("tool_use", tool_block("tu_1", "check_availability", {"x": 1}))),
            (["I have ", "nine o'clock."], message("end_turn", text_block("I have nine o'clock."))),
        ]
    )
    provider = await provider_factory(client)
    await provider.send_text("Any cleanings tomorrow?")
    events = await collect_until_done(provider)

    [call] = [e for e in events if isinstance(e, ToolCallRequested)]
    assert (call.call_id, call.name) == ("tu_1", "check_availability")
    assert AgentTextDone("I have nine o'clock.") in events

    # The second request carried the tool result back, marked as success.
    second = client.requests[1]["messages"]
    tool_result = second[-1]["content"][0]
    assert tool_result["tool_use_id"] == "tu_1"
    assert tool_result["is_error"] is False

    # Request shape: low effort, tools with eager streaming, refusal fallbacks on.
    first = client.requests[0]
    assert first["output_config"] == {"effort": "low"}
    assert first["fallbacks"] == "default"
    assert all(t["eager_input_streaming"] for t in first["tools"])


async def test_refusal_is_replaced_with_a_safe_reply(provider_factory: Any) -> None:
    client = FakeClient([(["partial"], message("refusal"))])
    provider = await provider_factory(client)
    await provider.send_text("something off limits")
    events = await collect_until_done(provider)
    assert AgentTextDone(REFUSAL_REPLY) in events
    assert provider._history[-1] == {"role": "assistant", "content": REFUSAL_REPLY}


async def test_interrupting_while_a_tool_is_pending_keeps_history_valid(
    provider_factory: Any,
) -> None:
    client = FakeClient([([], message("tool_use", tool_block("tu_9", "lookup_patient", {})))])
    provider = await provider_factory(client)
    await provider.send_text("It's Maria")

    async for event in provider.events():
        if isinstance(event, ToolCallRequested):
            break  # never answer it
    await provider.interrupt(audio_played_ms=None)
    await asyncio.sleep(0)

    last = provider._history[-1]
    assert last["role"] == "user"
    assert last["content"][0]["tool_use_id"] == "tu_9"
    assert last["content"][0]["is_error"] is True
