"""Gemini Live (native audio) as a speech-to-speech provider.

Gemini Live streams audio both ways over one WebSocket session. Its server-side
VAD decides when the caller has finished, and when the caller talks over the
model it stops generating and sends `interrupted`. This adapter maps that onto
the provider events the session understands:

* `interrupted` (or a voice-activity start) -> `CallerSpeechStarted`, which is
  the session's barge-in signal, followed by `ResponseDone(interrupted=True)`.
* Input transcription is buffered and emitted as one `CallerTranscript` when
  the model starts answering, so the transcript shows whole utterances.
* Output audio is already PCM16 at 24 kHz, the internal format.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, ClassVar

from google import genai
from google.genai import types

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
from callpilot.tools.registry import gemini_function_declarations

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-live"
DEFAULT_VOICE = "Kore"
INPUT_MIME = f"audio/pcm;rate={SAMPLE_RATE}"


def build_live_config(config: SessionConfig, voice: str) -> types.LiveConnectConfig:
    declarations = [
        types.FunctionDeclaration(**d) for d in gemini_function_declarations(config.tools)
    ]
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=config.instructions,
        tools=[types.Tool(function_declarations=declarations)],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                prefix_padding_ms=20,
                silence_duration_ms=500,
            ),
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        ),
        # Keeps long calls inside the context window instead of failing.
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
    )


class GeminiLiveProvider(RealtimeProvider):
    name: ClassVar[str] = "gemini"
    modes: ClassVar[frozenset[Mode]] = frozenset({"voice"})

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
    ) -> None:
        super().__init__(model=model)
        self._api_key = api_key
        self._voice = voice
        self._config: SessionConfig | None = None
        self._stack = contextlib.AsyncExitStack()
        self._session: Any = None
        self._receiver: asyncio.Task[None] | None = None

        self._responding = False
        self._agent_text: list[str] = []
        self._caller_text: list[str] = []
        self._caller_signalled = False  # CallerSpeechStarted already sent for this utterance
        self._next_turn_is_ours = False  # the next response answers greet()/send_text()
        self._discarding = False  # drop audio from a response we cancelled locally

    # Lifecycle -------------------------------------------------------------------

    async def connect(self, config: SessionConfig) -> None:
        if config.mode != "voice":
            raise ValueError("GeminiLiveProvider only supports voice mode")
        self._config = config
        client = genai.Client(api_key=self._api_key)
        live_config = build_live_config(config, config.voice or self._voice)
        self._session = await self._stack.enter_async_context(
            client.aio.live.connect(model=self.model, config=live_config)
        )
        self._receiver = asyncio.create_task(self._receive_loop(), name="gemini-receive")

    async def close(self) -> None:
        if self._receiver is not None:
            self._receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._receiver
        with contextlib.suppress(Exception):
            await self._stack.aclose()
        await super().close()

    # Input -----------------------------------------------------------------------

    async def greet(self) -> None:
        assert self._config is not None, "connect() first"
        self._next_turn_is_ours = True
        await self._session.send_realtime_input(
            text=f'(The call just connected. Greet the caller by saying: "{self._config.greeting}")'
        )

    async def send_audio(self, pcm: bytes) -> None:
        await self._session.send_realtime_input(audio=types.Blob(data=pcm, mime_type=INPUT_MIME))

    async def send_text(self, text: str) -> None:
        self._next_turn_is_ours = True
        await self._session.send_realtime_input(text=text)

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        await self._session.send_tool_response(
            function_responses=[types.FunctionResponse(id=call_id, name=name, response=result)]
        )

    async def interrupt(self, audio_played_ms: int | None) -> None:
        # Gemini cancels generation itself when it hears the caller, and it has
        # no truncate operation, so there is nothing to send. If the session
        # interrupts for another reason (typed text), drop the rest of the
        # current response locally.
        if self._responding:
            self._discarding = True
            self._finish_response(interrupted=True)

    # Output ----------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        try:
            while True:
                # receive() yields the messages of one model turn, then returns.
                async for message in self._session.receive():
                    self.handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("gemini live session ended: %s", exc)
            self._emit(ProviderError("The voice connection was lost.", fatal=True))

    def handle_message(self, message: types.LiveServerMessage) -> None:
        """Translate one server message into provider events (public for tests)."""
        activity = message.voice_activity
        if activity is not None:
            if activity.voice_activity_type == types.VoiceActivityType.ACTIVITY_START:
                self._caller_started()
            elif activity.voice_activity_type == types.VoiceActivityType.ACTIVITY_END:
                self._emit(CallerSpeechStopped())

        content = message.server_content
        if content is not None:
            if content.input_transcription and content.input_transcription.text:
                self._caller_text.append(content.input_transcription.text)
            if content.interrupted:
                self._caller_started()
                self._discarding = False
                if self._responding:
                    self._finish_response(interrupted=True)
            if content.model_turn and content.model_turn.parts and not self._discarding:
                for part in content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        self._start_response()
                        self._emit(AgentAudio(part.inline_data.data))
            if (
                content.output_transcription
                and content.output_transcription.text
                and not self._discarding
            ):
                self._start_response()
                self._agent_text.append(content.output_transcription.text)
                self._emit(AgentTextDelta(content.output_transcription.text))
            if content.turn_complete:
                self._discarding = False
                if self._responding:
                    self._finish_response(interrupted=False)

        if message.tool_call and message.tool_call.function_calls:
            self._start_response()
            for call in message.tool_call.function_calls:
                self._emit(ToolCallRequested(call.id or "", call.name or "", dict(call.args or {})))

        if message.go_away is not None:
            log.info("gemini will close the session in %s", message.go_away.time_left)

    # Helpers ---------------------------------------------------------------------

    def _caller_started(self) -> None:
        if not self._caller_signalled:
            self._caller_signalled = True
            self._emit(CallerSpeechStarted())

    def _start_response(self) -> None:
        if self._responding:
            return
        if self._next_turn_is_ours:
            # Answering greet() or typed text; the session already knows.
            self._next_turn_is_ours = False
            self._caller_text.clear()
        else:
            # Gemini only starts a new turn after the caller said something,
            # so this opens a caller turn even if transcription is lagging.
            text = "".join(self._caller_text).strip()
            self._caller_text.clear()
            self._emit(CallerTranscript(text, opens_turn=not self._caller_signalled))
        self._caller_signalled = False
        self._responding = True
        self._emit(ResponseStarted())

    def _finish_response(self, *, interrupted: bool) -> None:
        if self._agent_text and not interrupted:
            self._emit(AgentTextDone("".join(self._agent_text).strip()))
            self._agent_text.clear()
        self._responding = False
        self._emit(ResponseDone(interrupted=interrupted))
        if interrupted:
            self._agent_text.clear()
