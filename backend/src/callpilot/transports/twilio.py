"""Phone calls through Twilio Media Streams.

1. Twilio calls `POST /twilio/voice` when someone rings the number. We check
   Twilio's request signature and answer with TwiML that connects the call to
   our media WebSocket, passing the caller's number and a short-lived token.
2. Twilio opens `/twilio/media` and streams the caller's audio as 8 kHz
   mu-law. We decode and upsample to 24 kHz PCM16 for the session, and do the
   reverse for the agent's audio.
3. After each chunk of agent audio we send a `mark`. Twilio echoes it back once
   that audio has played, which tells the session how much the caller heard.
4. On barge-in the session asks the transport to clear audio, which becomes
   Twilio's `clear` message: everything buffered is dropped at once.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qsl
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect

from callpilot.audio.codecs import (
    Resampler,
    array_to_pcm16,
    mulaw_decode,
    mulaw_encode,
    pcm16_to_array,
)
from callpilot.calllog.summarizer import schedule_summary
from callpilot.providers.base import ProviderUnavailableError
from callpilot.session.call_session import CallSession
from callpilot.session.factory import build_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])

STREAM_TOKEN_TTL_S = 60


# Signatures -------------------------------------------------------------------


def twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """Twilio's X-Twilio-Signature: HMAC-SHA1 over the URL plus sorted POST params."""
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def stream_token(secret: str, *, now: float | None = None) -> str:
    """A short-lived token proving a media stream came from our own TwiML."""
    expires = int((now or time.time()) + STREAM_TOKEN_TTL_S)
    mac = hmac.new(secret.encode(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{mac}"


def stream_token_valid(token: str, secret: str, *, now: float | None = None) -> bool:
    expires, _, mac = token.partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    expected = hmac.new(secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


def _secret(request_or_ws: Request | WebSocket) -> str | None:
    token = request_or_ws.app.state.settings.twilio_auth_token
    return token.get_secret_value() if token else None


# Webhook ------------------------------------------------------------------------


@router.post("/voice")
async def incoming_call(request: Request) -> Response:
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    settings = request.app.state.settings
    base_url = (settings.public_base_url or str(request.base_url)).rstrip("/")
    secret = _secret(request)

    if secret:
        signed_url = f"{base_url}/twilio/voice" if settings.public_base_url else str(request.url)
        expected = twilio_signature(signed_url, params, secret)
        given = request.headers.get("X-Twilio-Signature", "")
        if not hmac.compare_digest(expected, given):
            log.warning("rejected a /twilio/voice request with a bad signature")
            return Response("Invalid signature", status_code=403)
    else:
        log.warning("TWILIO_AUTH_TOKEN is not set: phone webhooks are not verified")

    stream_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    parameters = {"caller": params.get("From", "")}
    if secret:
        parameters["token"] = stream_token(secret)
    parameter_xml = "".join(
        f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in parameters.items()
    )
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(stream_url + '/twilio/media')}>"
        f"{parameter_xml}</Stream></Connect></Response>"
    )
    return Response(twiml, media_type="application/xml")


# Media stream ----------------------------------------------------------------


class TwilioTransport:
    """CallTransport that speaks Twilio's Media Streams protocol."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self.stream_sid: str | None = None
        self._downsample = Resampler(24_000, 8_000)
        self._response = 0
        self._sent_ms = 0.0

    async def _send(self, message: dict[str, Any]) -> None:
        if self.stream_sid is not None:
            await self._ws.send_text(json.dumps({**message, "streamSid": self.stream_sid}))

    async def send_audio(self, pcm: bytes) -> None:
        samples = self._downsample.process(pcm16_to_array(pcm))
        payload = base64.b64encode(mulaw_encode(samples)).decode()
        await self._send({"event": "media", "media": {"payload": payload}})
        self._sent_ms += len(pcm) / 48  # 24 kHz PCM16: 48 bytes per ms
        await self._send(
            {"event": "mark", "mark": {"name": f"{self._response}:{int(self._sent_ms)}"}}
        )

    async def clear_audio(self) -> None:
        await self._send({"event": "clear"})

    async def send_event(self, event: dict[str, Any]) -> None:
        # The phone has no screen; only response boundaries matter here.
        if event.get("type") == "agent.response":
            self._response += 1
            self._sent_ms = 0.0

    def played_ms(self, mark_name: str) -> int | None:
        """Milliseconds of the current response played, from an echoed mark."""
        response, _, played = mark_name.partition(":")
        if response != str(self._response) or not played.isdigit():
            return None  # a mark from an earlier, finished response
        return int(played)


@router.websocket("/media")
async def media_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    app = websocket.app
    transport = TwilioTransport(websocket)
    upsample = Resampler(8_000, 24_000)
    session: CallSession | None = None
    reason = "caller_hung_up"
    try:
        while session is None or not session.ended:
            message = json.loads(await websocket.receive_text())
            event = message.get("event")
            if event == "start":
                session = await _start_session(websocket, transport, message["start"])
                if session is None:
                    return
            elif event == "media" and session is not None:
                audio = mulaw_decode(base64.b64decode(message["media"]["payload"]))
                await session.caller_audio(array_to_pcm16(upsample.process(audio)))
            elif event == "mark" and session is not None:
                played = transport.played_ms(message["mark"]["name"])
                if played is not None:
                    session.playback_progress(played)
            elif event == "stop":
                break
    except WebSocketDisconnect:
        reason = "caller_disconnected"
    except Exception:
        log.exception("phone call failed")
        reason = "server_error"
    finally:
        if session is not None:
            await session.hang_up(reason=reason)
            schedule_summary(app, session.call_id)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


async def _start_session(
    websocket: WebSocket, transport: TwilioTransport, start: dict[str, Any]
) -> CallSession | None:
    app = websocket.app
    params = start.get("customParameters") or {}
    secret = _secret(websocket)
    if secret and not stream_token_valid(params.get("token", ""), secret):
        log.warning("rejected a media stream without a valid token")
        await websocket.close(code=1008)
        return None
    transport.stream_sid = start.get("streamSid")
    try:
        provider = app.state.provider_factory(app.state.settings, "voice")
    except ProviderUnavailableError:
        log.error("phone call arrived but no voice provider is configured")
        await websocket.close(code=1011)
        return None
    session, config = build_session(
        conn=app.state.db,
        tz=app.state.tz,
        provider=provider,
        transport=transport,
        mode="voice",
        channel="phone",
        clock=app.state.clock,
        caller_number=params.get("caller") or None,
    )
    await session.start(config)
    return session
