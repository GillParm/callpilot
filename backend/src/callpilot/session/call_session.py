"""CallSession: one live call, from greeting to hang-up.

The session sits between a transport (browser WebSocket, Twilio stream, or a
test harness) and a model provider. It forwards caller input to the provider,
runs every tool call through the guardrails, streams transcripts and tool
activity to the transport, measures latency per turn and writes it all to the
call log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from callpilot.agent.guardrails import GuardedToolExecutor
from callpilot.agent.state import ConversationState
from callpilot.calllog.recorder import CallRecorder
from callpilot.providers.base import (
    AgentAudio,
    AgentTextDelta,
    AgentTextDone,
    CallerSpeechStarted,
    CallerSpeechStopped,
    CallerTranscript,
    Mode,
    ProviderError,
    ProviderEvent,
    RealtimeProvider,
    ResponseDone,
    ResponseStarted,
    SessionConfig,
    ToolCallRequested,
)

log = logging.getLogger(__name__)

Event = dict[str, Any]


class CallTransport(Protocol):
    """Where the session sends things the caller should see or hear."""

    async def send_event(self, event: Event) -> None: ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def clear_audio(self) -> None:
        """Drop any agent audio that is queued but not yet played."""


@dataclass
class TurnTimer:
    """Latency bookkeeping for one caller turn."""

    index: int
    caller_done_at: float
    first_output_at: float | None = None
    tool_ms: float = 0.0
    tools: list[str] = field(default_factory=list)


class CallSession:
    def __init__(
        self,
        *,
        call_id: str,
        mode: Mode,
        provider: RealtimeProvider,
        executor: GuardedToolExecutor,
        recorder: CallRecorder,
        transport: CallTransport,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.call_id = call_id
        self.mode = mode
        self.provider = provider
        self.executor = executor
        self.recorder = recorder
        self.transport = transport
        self._monotonic = monotonic

        self._pump_task: asyncio.Task[None] | None = None
        self._ended = asyncio.Event()
        self._responding = False
        self._agent_text: list[str] = []
        self._turn: TurnTimer | None = None
        self._turn_count = 0
        self._played_ms: int | None = None

    @property
    def state(self) -> ConversationState:
        return self.executor.state

    @property
    def ended(self) -> bool:
        return self._ended.is_set()

    # Lifecycle ---------------------------------------------------------------

    async def start(self, config: SessionConfig) -> None:
        await self.provider.connect(config)
        self._pump_task = asyncio.create_task(self._pump(), name=f"pump-{self.call_id}")
        await self.transport.send_event(
            {
                "type": "call.started",
                "call_id": self.call_id,
                "mode": self.mode,
                "provider": self.provider.name,
                "model": self.provider.model,
            }
        )
        await self.provider.greet()

    async def hang_up(self, reason: str = "caller_hung_up") -> None:
        if self._ended.is_set():
            return
        self._ended.set()
        self.recorder.record("call_ended", {"reason": reason})
        self.recorder.finish(outcome=self.state.outcome)
        await self.provider.close()
        if self._pump_task is not None and self._pump_task is not asyncio.current_task():
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
        with contextlib.suppress(Exception):
            await self.transport.send_event(
                {"type": "call.ended", "reason": reason, "outcome": self.state.outcome}
            )

    async def wait_ended(self) -> None:
        await self._ended.wait()

    # Caller input ------------------------------------------------------------

    async def caller_text(self, text: str) -> None:
        text = text.strip()
        if not text or self.ended:
            return
        if self._responding:
            await self._interrupt(trigger="caller_text", audio_played_ms=None)
        self.state.note_caller_turn()
        self._begin_turn()
        self.recorder.record("transcript", {"role": "caller", "text": text})
        await self.transport.send_event({"type": "transcript", "role": "caller", "text": text})
        await self.provider.send_text(text)

    async def caller_audio(self, pcm: bytes) -> None:
        if self.ended or self.mode != "voice":
            return
        await self.provider.send_audio(pcm)

    def playback_progress(self, played_ms: int) -> None:
        """The client reports how much of the current agent response it has played."""
        self._played_ms = max(0, played_ms)

    def record_client_latency(self, message: dict[str, Any]) -> None:
        """Latency as the caller perceived it, measured in the browser."""
        perceived = message.get("perceived_ms")
        if isinstance(perceived, int | float) and 0 <= perceived < 60_000:
            self.recorder.record(
                "client_latency", {"turn": message.get("turn"), "perceived_ms": int(perceived)}
            )

    # Provider events ---------------------------------------------------------

    async def _pump(self) -> None:
        try:
            async for event in self.provider.events():
                await self._handle(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("provider event pump failed", extra={"call_id": self.call_id})
            await self.transport.send_event(
                {"type": "error", "message": "The voice service hit an error."}
            )
        if not self.ended:
            await self.hang_up(reason="provider_closed")

    async def _handle(self, event: ProviderEvent) -> None:
        match event:
            case ResponseStarted():
                self._responding = True
            case AgentTextDelta(text=text):
                self._agent_text.append(text)
                if self.mode == "text":
                    await self._first_output()
                await self.transport.send_event(
                    {"type": "transcript.delta", "role": "agent", "text": text}
                )
            case AgentTextDone(text=text):
                self._agent_text.clear()
                self.recorder.record("transcript", {"role": "agent", "text": text})
                await self.transport.send_event(
                    {"type": "transcript", "role": "agent", "text": text}
                )
            case AgentAudio(pcm=pcm):
                await self._first_output()
                await self.transport.send_audio(pcm)
            case ToolCallRequested():
                await self._run_tool(event)
            case ResponseDone(interrupted=interrupted):
                self._responding = False
                if interrupted and self._agent_text:
                    partial = "".join(self._agent_text)
                    self._agent_text.clear()
                    self.recorder.record(
                        "transcript", {"role": "agent", "text": partial, "interrupted": True}
                    )
                await self.transport.send_event({"type": "agent.done", "interrupted": interrupted})
            case CallerSpeechStarted() | CallerSpeechStopped() | CallerTranscript():
                pass  # voice-only; handled once audio support lands
            case ProviderError(message=message, fatal=fatal):
                self.recorder.record("error", {"message": message, "fatal": fatal})
                await self.transport.send_event({"type": "error", "message": message})
                if fatal:
                    await self.hang_up(reason="provider_error")

    async def _run_tool(self, request: ToolCallRequested) -> None:
        await self.transport.send_event(
            {
                "type": "tool.call",
                "id": request.call_id,
                "name": request.name,
                "arguments": request.arguments,
            }
        )
        outcome = self.executor.execute(request.call_id, request.name, request.arguments)
        if self._turn is not None:
            self._turn.tool_ms += outcome.duration_ms
            self._turn.tools.append(request.name)
        payload = {
            "id": outcome.call_id,
            "name": outcome.name,
            "arguments": outcome.arguments,
            "result": outcome.result,
            "ok": outcome.ok,
            "guardrail": outcome.guardrail,
            "duration_ms": outcome.duration_ms,
        }
        self.recorder.record("tool_call", payload)
        await self.transport.send_event({"type": "tool.result", **payload})
        await self.provider.send_tool_result(request.call_id, request.name, outcome.result)

    # Interruptions and latency --------------------------------------------------

    async def _interrupt(self, *, trigger: str, audio_played_ms: int | None) -> None:
        await self.transport.clear_audio()
        await self.provider.interrupt(audio_played_ms)
        self.recorder.record("barge_in", {"trigger": trigger, "audio_played_ms": audio_played_ms})
        await self.transport.send_event({"type": "barge_in", "trigger": trigger})

    def _begin_turn(self) -> None:
        self._turn_count += 1
        self._turn = TurnTimer(index=self._turn_count, caller_done_at=self._monotonic())

    async def _first_output(self) -> None:
        turn = self._turn
        if turn is None or turn.first_output_at is not None:
            return
        turn.first_output_at = self._monotonic()
        metric = {
            "turn": turn.index,
            "kind": "time_to_first_audio" if self.mode == "voice" else "time_to_first_token",
            "latency_ms": round((turn.first_output_at - turn.caller_done_at) * 1000),
            "tool_ms": round(turn.tool_ms, 2),
            "tools": list(turn.tools),
        }
        self.recorder.record("latency", metric)
        await self.transport.send_event({"type": "metrics.turn", **metric})
