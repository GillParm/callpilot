"""Phone calls: the TwiML webhook, signatures and the Media Streams bridge."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime
from typing import Any, ClassVar

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from callpilot.audio.codecs import mulaw_encode
from callpilot.config import Settings
from callpilot.main import create_app
from callpilot.providers.base import (
    AgentAudio,
    CallerSpeechStarted,
    Mode,
    RealtimeProvider,
    ResponseStarted,
    SessionConfig,
)
from callpilot.transports.twilio import stream_token, stream_token_valid, twilio_signature

TOKEN = "twilio-test-token"
BASE = "https://callpilot.example.com"


class PhoneProvider(RealtimeProvider):
    """Greets with 40 ms of audio, then treats any caller audio as a barge-in."""

    name: ClassVar[str] = "phone-fake"
    modes: ClassVar[frozenset[Mode]] = frozenset({"voice"})

    def __init__(self) -> None:
        super().__init__(model="fake")
        self.audio_in: list[bytes] = []
        self.interrupts: list[int | None] = []

    async def connect(self, config: SessionConfig) -> None:
        pass

    async def greet(self) -> None:
        self._emit(ResponseStarted())
        self._emit(AgentAudio(b"\x10\x00" * 960))  # 40 ms at 24 kHz

    async def send_audio(self, pcm: bytes) -> None:
        if not self.audio_in:
            self._emit(CallerSpeechStarted())
        self.audio_in.append(pcm)

    async def send_text(self, text: str) -> None:
        pass

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        pass

    async def interrupt(self, audio_played_ms: int | None) -> None:
        self.interrupts.append(audio_played_ms)


@pytest.fixture
def provider() -> PhoneProvider:
    return PhoneProvider()


@pytest.fixture
def client(now: datetime, provider: PhoneProvider) -> Iterator[TestClient]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, DATABASE_PATH=":memory:", TWILIO_AUTH_TOKEN=TOKEN, PUBLIC_BASE_URL=BASE
    )
    app = create_app(settings, provider_factory=lambda s, m: provider, clock=lambda: now)
    with TestClient(app) as test_client:
        yield test_client


# Signatures -------------------------------------------------------------------


def test_signature_changes_with_any_input() -> None:
    params = {"CallSid": "CA123", "From": "+15550100"}
    signature = twilio_signature(f"{BASE}/twilio/voice", params, TOKEN)
    assert signature == twilio_signature(
        f"{BASE}/twilio/voice", dict(reversed(params.items())), TOKEN
    )
    assert signature != twilio_signature(f"{BASE}/twilio/voice", {**params, "From": "+1"}, TOKEN)
    assert signature != twilio_signature(f"{BASE}/twilio/voice", params, "other-token")


def test_stream_tokens_expire_and_cannot_be_forged() -> None:
    token = stream_token(TOKEN, now=1000)
    assert stream_token_valid(token, TOKEN, now=1030)
    assert not stream_token_valid(token, TOKEN, now=2000)
    assert not stream_token_valid(token, "wrong-secret", now=1030)
    assert not stream_token_valid("garbage", TOKEN, now=1030)


# Webhook ---------------------------------------------------------------------------


def test_webhook_rejects_unsigned_requests(client: TestClient) -> None:
    response = client.post("/twilio/voice", data={"From": "+15550100"})
    assert response.status_code == 403


def test_webhook_connects_the_call_to_the_media_stream(client: TestClient) -> None:
    params = {"CallSid": "CA123", "From": "+15550100"}
    signature = twilio_signature(f"{BASE}/twilio/voice", params, TOKEN)
    response = client.post("/twilio/voice", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    body = response.text
    assert '<Stream url="wss://callpilot.example.com/twilio/media">' in body
    assert '<Parameter name="caller" value="+15550100"/>' in body
    assert '<Parameter name="token"' in body


# Media stream ------------------------------------------------------------------------


def start_message(token: str) -> dict[str, Any]:
    return {
        "event": "start",
        "start": {
            "streamSid": "MZ1",
            "callSid": "CA123",
            "customParameters": {"caller": "+15550100", "token": token},
        },
    }


def test_media_stream_round_trip(client: TestClient, provider: PhoneProvider) -> None:
    with client.websocket_connect("/twilio/media") as ws:
        ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        ws.send_json(start_message(stream_token(TOKEN)))

        # The greeting arrives as 8 kHz mu-law, followed by a mark.
        media = ws.receive_json()
        assert media["event"] == "media"
        assert media["streamSid"] == "MZ1"
        assert len(base64.b64decode(media["media"]["payload"])) == 320  # 40 ms at 8 kHz
        mark = ws.receive_json()
        assert mark == {"event": "mark", "mark": {"name": "1:40"}, "streamSid": "MZ1"}

        # Twilio confirms 20 ms has played, then the caller talks over the agent.
        ws.send_json({"event": "mark", "streamSid": "MZ1", "mark": {"name": "1:20"}})
        tone = (8000 * np.sin(np.linspace(0, 40 * np.pi, 160))).astype(np.int16)
        payload = base64.b64encode(mulaw_encode(tone)).decode()
        ws.send_json({"event": "media", "streamSid": "MZ1", "media": {"payload": payload}})
        assert ws.receive_json() == {"event": "clear", "streamSid": "MZ1"}

        ws.send_json({"event": "stop", "streamSid": "MZ1"})

    assert len(provider.audio_in[0]) == 960  # 20 ms at 8 kHz became 20 ms at 24 kHz
    assert provider.interrupts == [20], "the agent's turn is trimmed to what was heard"
    db = client.app.state.db  # type: ignore[attr-defined]
    call = db.execute("SELECT channel, caller_number FROM calls").fetchone()
    assert (call["channel"], call["caller_number"]) == ("phone", "+15550100")


def test_media_stream_without_a_valid_token_is_refused(
    client: TestClient, provider: PhoneProvider
) -> None:
    with client.websocket_connect("/twilio/media") as ws:
        ws.send_json(start_message("123.forged"))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert client.app.state.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0  # type: ignore[attr-defined]
