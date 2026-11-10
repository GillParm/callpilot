"""Browser calls over a WebSocket.

Wire protocol (one socket per call, `/ws/call?mode=voice|text`):

* client -> server
  - binary frames: caller audio, PCM16 mono 24 kHz
  - `{"type": "caller.text", "text": "..."}`: a typed caller message
  - `{"type": "playback", "played_ms": 1234}`: how much agent audio has played
  - `{"type": "client.latency", "turn": 3, "perceived_ms": 820}`
  - `{"type": "hangup"}`
* server -> client
  - binary frames: agent audio, PCM16 mono 24 kHz
  - JSON events: `call.started`, `transcript`, `transcript.delta`, `tool.call`,
    `tool.result`, `metrics.turn`, `barge_in`, `audio.clear`, `agent.done`,
    `error`, `call.ended`
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from callpilot.providers.base import Mode, ProviderUnavailableError
from callpilot.session.call_session import CallSession
from callpilot.session.factory import build_session

log = logging.getLogger(__name__)
router = APIRouter()


class BrowserTransport:
    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket

    @property
    def open(self) -> bool:
        return self._ws.application_state == WebSocketState.CONNECTED

    async def send_event(self, event: dict[str, Any]) -> None:
        if self.open:
            await self._ws.send_text(json.dumps(event, default=str))

    async def send_audio(self, pcm: bytes) -> None:
        if self.open:
            await self._ws.send_bytes(pcm)

    async def clear_audio(self) -> None:
        await self.send_event({"type": "audio.clear"})


@router.websocket("/ws/call")
async def browser_call(websocket: WebSocket, mode: Mode = "voice") -> None:
    await websocket.accept()
    app = websocket.app
    transport = BrowserTransport(websocket)
    try:
        provider = app.state.provider_factory(app.state.settings, mode)
    except ProviderUnavailableError as exc:
        await transport.send_event({"type": "error", "message": str(exc), "fatal": True})
        await websocket.close(code=1011)
        return

    session, config = build_session(
        conn=app.state.db,
        tz=app.state.tz,
        provider=provider,
        transport=transport,
        mode=mode,
        channel="browser",
        clock=app.state.clock,
    )
    reason = "caller_hung_up"
    try:
        await session.start(config)
        while not session.ended:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                reason = "caller_disconnected"
                break
            if message.get("bytes") is not None:
                await session.caller_audio(message["bytes"])
            elif message.get("text") and not await _handle_control(session, message["text"]):
                break
    except WebSocketDisconnect:
        reason = "caller_disconnected"
    except Exception:
        log.exception("browser call failed", extra={"call_id": session.call_id})
        reason = "server_error"
    finally:
        await session.hang_up(reason=reason)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


async def _handle_control(session: CallSession, raw: str) -> bool:
    """Apply one JSON control message. Returns False when the caller hangs up."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return True
    match message.get("type"):
        case "caller.text":
            await session.caller_text(str(message.get("text", "")))
        case "playback":
            session.playback_progress(int(message.get("played_ms", 0)))
        case "client.latency":
            session.record_client_latency(message)
        case "hangup":
            return False
    return True
