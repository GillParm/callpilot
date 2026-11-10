"""The MCP server, exercised in-process through a real MCP client."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from mcp import Client

from callpilot.clinic.repository import ClinicRepository
from callpilot.mcp_server import build_server
from callpilot.tools.clinic_tools import ClinicTools


@pytest.fixture
def server(conn: sqlite3.Connection, tz: ZoneInfo, now: datetime) -> Any:
    return build_server(ClinicTools(ClinicRepository(conn, tz), clock=lambda: now))


async def call(client: Client, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result
    assert result.structured_content is not None
    return dict(result.structured_content.get("result", result.structured_content))


async def test_lists_the_five_clinic_tools(server: Any) -> None:
    async with Client(server, raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {
        "check_availability",
        "lookup_patient",
        "book_appointment",
        "cancel_appointment",
        "escalate_to_human",
    }
    assert tools["check_availability"].annotations.read_only_hint is True
    assert tools["cancel_appointment"].annotations.destructive_hint is True
    assert "caller_confirmed" not in tools["book_appointment"].input_schema["properties"]


async def test_an_agent_can_book_through_mcp(server: Any, conn: sqlite3.Connection) -> None:
    async with Client(server, raise_exceptions=True) as client:
        patient = await call(
            client, "lookup_patient", {"full_name": "James Carter", "date_of_birth": "1975-11-02"}
        )
        slots = await call(
            client, "check_availability", {"appointment_type": "checkup", "date_from": "2026-11-11"}
        )
        booked = await call(
            client,
            "book_appointment",
            {
                "patient_id": patient["patient_id"],
                "slot_id": slots["slots"][0]["slot_id"],
                "reason": "checkup",
            },
        )
    assert booked["ok"] is True
    assert booked["status"] == "booked"
    count = conn.execute(
        "SELECT COUNT(*) FROM appointments WHERE patient_id = 'P-1002' AND status = 'booked'"
    ).fetchone()[0]
    assert count >= 1


async def test_domain_errors_come_back_as_readable_results(server: Any) -> None:
    async with Client(server, raise_exceptions=True) as client:
        result = await call(
            client,
            "book_appointment",
            {"patient_id": "P-1002", "slot_id": "S-made-up", "reason": "checkup"},
        )
    assert result == {
        "ok": False,
        "error": {"code": "unknown_slot", "message": "That slot_id does not exist."},
    }
