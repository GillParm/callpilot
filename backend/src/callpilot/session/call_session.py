"""CallSession: one live call, from greeting to hang-up.

The session sits between a transport (browser WebSocket, Twilio stream, or a
test harness) and a model provider. It forwards caller input to the provider,
runs every tool call through the guardrails, streams transcripts and tool
activity to the transport, handles barge-in, measures latency per turn and
writes it all to the call log.

Barge-in, step by step:

1. The provider reports that the caller started speaking (`CallerSpeechStarted`).
2. If the agent is mid-response, or its audio is still queued on the caller's
   side, the session tells the transport to drop queued audio and asks the
   provider to cancel the response.
3. The provider is told how many milliseconds of the response the caller
   actually heard (reported by the client), so it can trim the agent's turn in
   the conversation history to what was really said.
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
from callpilot.audio.codecs import pcm16_to_array
from callpilot.audio.vad import EnergyVAD
from callpilot.calllog.recorder import CallRecorder
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
    ProviderEvent,
    RealtimeProvider,
    ResponseDone,
    ResponseStarted,
    SessionConfig,
    ToolCallRequested,
)

log = logging.getLogger(__name__)

Event = dict[str, Any]

# PCM16 mono: 2 bytes per sample.
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000
# Client playback reports arrive every ~100 ms, so allow for that much lag
# before deciding the caller is no longer hearing the agent.
PLAYBACK_REPORT_SLACK_MS = 150
# A VAD timestamp older than this belongs to an earlier turn, not this one.
MAX_PLAUSIBLE_LATENCY_S = 15.0
# Faster than any real response: the caller's speech overlapped the reply.
OVERLAP_THRESHOLD_MS = 150


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
    started_at: float
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

        # Agent output state
        self._responding = False
        self._agent_text: list[str] = []
        self._response_audio_ms = 0.0  # audio sent for the current response
        self._played_ms: int | None = None  # audio the client says it has played

        # Caller turn state
        self._turn: TurnTimer | None = None
        self._turn_count = 0
        self._caller_turn_open = False
        self._vad = EnergyVAD(sample_rate=SAMPLE_RATE)
        self._speech_stopped_at: float | None = None

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
        if self._agent_audible():
            await self._barge_in(trigger="caller_text")
        self._open_caller_turn()
        self.recorder.record("transcript", {"role": "caller", "text": text})
        await self.transport.send_event({"type": "transcript", "role": "caller", "text": text})
        await self.provider.send_text(text)

    async def caller_audio(self, pcm: bytes) -> None:
        if self.ended or self.mode != "voice":
            return
        self._vad.process(pcm16_to_array(pcm), now=self._monotonic())
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
                self._caller_turn_open = False
                self._response_audio_ms = 0.0
                self._played_ms = None
                await self.transport.send_event({"type": "agent.response"})
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
                self._response_audio_ms += len(pcm) / BYTES_PER_MS
                await self.transport.send_audio(pcm)
            case ToolCallRequested():
                await self._run_tool(event)
            case ResponseDone(interrupted=interrupted):
                await self._response_done(interrupted)
            case CallerSpeechStarted():
                if self._agent_audible():
                    await self._barge_in(trigger="caller_speech")
                self._open_caller_turn()
                self._speech_stopped_at = None
            case CallerSpeechStopped():
                self._speech_stopped_at = self._monotonic()
            case CallerTranscript(text=text, opens_turn=opens_turn):
                if opens_turn:
                    self._open_caller_turn()
                if text:  # empty means "the caller spoke" with no transcript yet
                    self.recorder.record("transcript", {"role": "caller", "text": text})
                    await self.transport.send_event(
                        {"type": "transcript", "role": "caller", "text": text}
                    )
            case ProviderError(message=message, fatal=fatal):
                self.recorder.record("error", {"message": message, "fatal": fatal})
                await self.transport.send_event({"type": "error", "message": message})
                if fatal:
                    await self.hang_up(reason="provider_error")

    async def _response_done(self, interrupted: bool) -> None:
        self._responding = False
        if interrupted and self._agent_text:
            partial = "".join(self._agent_text)
            self._agent_text.clear()
            self.recorder.record(
                "transcript", {"role": "agent", "text": partial, "interrupted": True}
            )
            await self.transport.send_event(
                {"type": "transcript", "role": "agent", "text": partial, "interrupted": True}
            )
        await self.transport.send_event({"type": "agent.done", "interrupted": interrupted})

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

    # Barge-in ----------------------------------------------------------------

    def _agent_audible(self) -> bool:
        """Is the agent still talking, from the caller's point of view?"""
        if self._responding:
            return True
        if self._response_audio_ms == 0:
            return False
        played = self._played_ms if self._played_ms is not None else 0
        return played + PLAYBACK_REPORT_SLACK_MS < self._response_audio_ms

    async def _barge_in(self, *, trigger: str) -> None:
        heard_ms = None
        if self._response_audio_ms:
            heard_ms = int(min(self._played_ms or 0, self._response_audio_ms))
        await self.transport.clear_audio()
        await self.provider.interrupt(heard_ms)
        self._response_audio_ms = 0.0
        self.recorder.record("barge_in", {"trigger": trigger, "audio_played_ms": heard_ms})
        await self.transport.send_event(
            {"type": "barge_in", "trigger": trigger, "audio_played_ms": heard_ms}
        )

    # Latency -----------------------------------------------------------------

    def _open_caller_turn(self) -> None:
        """Start a new caller turn, unless one is already in progress."""
        if self._caller_turn_open:
            return
        self._caller_turn_open = True
        self.state.note_caller_turn()
        self._turn_count += 1
        self._turn = TurnTimer(index=self._turn_count, started_at=self._monotonic())

    async def _first_output(self) -> None:
        turn = self._turn
        if turn is None or turn.first_output_at is not None:
            return
        now = self._monotonic()
        turn.first_output_at = now
        metric: Event = {
            "turn": turn.index,
            "tool_ms": round(turn.tool_ms, 2),
            "tools": list(turn.tools),
        }
        if self.mode == "text":
            metric["kind"] = "time_to_first_token"
            metric["latency_ms"] = round((now - turn.started_at) * 1000)
        else:
            # Measure from the caller's last voiced audio, so the number includes
            # end-of-speech detection, just like the caller experiences it.
            spoke_until = self._vad.last_voiced_at
            if spoke_until is None or now - spoke_until > MAX_PLAUSIBLE_LATENCY_S:
                spoke_until = self._speech_stopped_at or turn.started_at
            metric["kind"] = "time_to_first_audio"
            metric["latency_ms"] = round((now - spoke_until) * 1000)
            # The caller was still talking when the agent started (the provider
            # cut their sentence at a pause). Not a real response time.
            metric["overlapped"] = metric["latency_ms"] < OVERLAP_THRESHOLD_MS
            if self._speech_stopped_at is not None:
                metric["endpointing_ms"] = max(
                    0, round((self._speech_stopped_at - spoke_until) * 1000)
                )
                metric["response_ms"] = round((now - self._speech_stopped_at) * 1000)
        self.recorder.record("latency", metric)
        await self.transport.send_event({"type": "metrics.turn", **metric})
