from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from callpilot.config import Settings
from callpilot.main import create_app
from callpilot.providers.base import Mode, RealtimeProvider
from callpilot.providers.scripted import ScriptedProvider
from tests.test_simulated_caller import BOOKING_SCRIPT


def offline_settings(**overrides: Any) -> Settings:
    """Settings that ignore any local .env, so tests never see real keys."""
    return Settings(_env_file=None, DATABASE_PATH=":memory:", **overrides)  # type: ignore[call-arg]


@pytest.fixture
def client(now: datetime) -> Iterator[TestClient]:
    def scripted(settings: Settings, mode: Mode) -> RealtimeProvider:
        return ScriptedProvider(BOOKING_SCRIPT)

    app = create_app(offline_settings(), provider_factory=scripted, clock=lambda: now)
    with TestClient(app) as test_client:
        yield test_client


def read_until(ws: WebSocketTestSession, event_type: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    while True:
        event = ws.receive_json()
        seen.append(event)
        if event["type"] == event_type:
            return seen


def test_config_lists_demo_patients_and_providers(client: TestClient) -> None:
    body = client.get("/api/config").json()
    assert body["clinic"]["name"] == "Brightside Dental"
    assert {"name": "Maria Lopez", "date_of_birth": "1988-03-14"} in body["demo_patients"]
    assert body["providers"] == {"voice": None, "text": None}


def test_text_call_over_websocket_books_an_appointment(client: TestClient) -> None:
    with client.websocket_connect("/ws/call?mode=text") as ws:
        started = ws.receive_json()
        assert started["type"] == "call.started"
        assert started["provider"] == "scripted"
        read_until(ws, "agent.done")  # greeting

        for line in (
            "Can I book a cleaning tomorrow morning?",
            "Nine please. Maria Lopez, March 14 1988.",
            "Yes.",
        ):
            ws.send_json({"type": "caller.text", "text": line})
            events = read_until(ws, "agent.done")
        assert any(e["type"] == "tool.result" and e["result"].get("status") == "booked"
                   for e in events)  # fmt: skip
        ws.send_json({"type": "hangup"})
        ended = read_until(ws, "call.ended")[-1]
        assert ended["outcome"] == "booked"

    db = client.app.state.db  # type: ignore[attr-defined]
    call = db.execute("SELECT * FROM calls").fetchone()
    assert call["outcome"] == "booked"
    assert call["channel"] == "browser"


def test_call_without_a_configured_provider_fails_clearly(now: datetime) -> None:
    app = create_app(offline_settings(), clock=lambda: now)
    with TestClient(app) as test_client, test_client.websocket_connect("/ws/call?mode=text") as ws:
        error = ws.receive_json()
    assert error["type"] == "error"
    assert error["fatal"] is True
    assert ".env" in error["message"]
