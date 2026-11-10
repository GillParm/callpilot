"""The clinic tools as an MCP server, so other agents can use them.

The same five tools the voice agent calls, backed by the same database. MCP
clients are agents, not callers on a phone line, so the conversation
guardrails (read-back and confirmation) don't apply here; the domain rules do:
slots must exist and be free, appointments must belong to the patient, and
lookups need both the name and the date of birth.

    # stdio, for desktop agent apps
    uv run callpilot-mcp
    # streamable HTTP on localhost:8001/mcp
    uv run callpilot-mcp --http --port 8001
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from callpilot.clinic.profile import CLINIC, AppointmentType
from callpilot.clinic.repository import ClinicRepository
from callpilot.config import get_settings
from callpilot.db import connect, init_db
from callpilot.db.seed import seed_all
from callpilot.tools.clinic_tools import ClinicTools, ToolError
from callpilot.tools.schemas import (
    BookAppointmentArgs,
    CancelAppointmentArgs,
    CheckAvailabilityArgs,
    EscalateArgs,
    LookupPatientArgs,
    TimeOfDay,
    Urgency,
)

JSON = dict[str, Any]


def _run(action: Callable[[], JSON]) -> JSON:
    """Turn domain errors into a result the calling agent can read and act on."""
    try:
        return {"ok": True, **action()}
    except ToolError as error:
        return {"ok": False, "error": {"code": error.code, "message": error.message}}


def build_server(tools: ClinicTools) -> MCPServer:
    server = MCPServer(
        name="callpilot-clinic",
        title=f"{CLINIC.name} scheduling",
        instructions=(
            f"Scheduling tools for {CLINIC.name}, a fictional dental clinic. Look a patient up "
            "with lookup_patient before booking or cancelling, only book slot_ids returned by "
            "check_availability, and confirm details with the person you are acting for "
            "before calling book_appointment or cancel_appointment."
        ),
    )
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool(annotations=read_only)
    def check_availability(
        appointment_type: Annotated[AppointmentType, Field(description="What the visit is for.")],
        date_from: Annotated[date, Field(description="First day to search (YYYY-MM-DD).")],
        date_to: Annotated[
            date | None, Field(description="Last day to search, inclusive; max 14 days.")
        ] = None,
        time_of_day: TimeOfDay = "any",
        dentist_name: str | None = None,
    ) -> JSON:
        """Find open appointment slots. Returns slot_ids to book with."""
        args = CheckAvailabilityArgs(
            appointment_type=appointment_type,
            date_from=date_from,
            date_to=date_to,
            time_of_day=time_of_day,
            dentist_name=dentist_name,
        )
        return _run(lambda: tools.check_availability(args))

    @server.tool(annotations=read_only)
    def lookup_patient(
        full_name: Annotated[str, Field(description="Patient's first and last name.")],
        date_of_birth: Annotated[date, Field(description="Date of birth (YYYY-MM-DD).")],
    ) -> JSON:
        """Find a patient by name and date of birth, with their upcoming appointments."""
        args = LookupPatientArgs(full_name=full_name, date_of_birth=date_of_birth)
        return _run(lambda: tools.lookup_patient(args))

    @server.tool(annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=False))
    def book_appointment(
        patient_id: Annotated[str, Field(description="From lookup_patient.")],
        slot_id: Annotated[str, Field(description="From check_availability.")],
        reason: Annotated[str, Field(description="Short reason for the visit.")],
    ) -> JSON:
        """Book a free slot for a patient."""
        args = BookAppointmentArgs(patient_id=patient_id, slot_id=slot_id, reason=reason)
        return _run(lambda: tools.book_appointment(args))

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True))
    def cancel_appointment(
        patient_id: Annotated[str, Field(description="From lookup_patient.")],
        appointment_id: Annotated[str, Field(description="From lookup_patient.")],
    ) -> JSON:
        """Cancel one of a patient's upcoming appointments."""
        args = CancelAppointmentArgs(patient_id=patient_id, appointment_id=appointment_id)
        return _run(lambda: tools.cancel_appointment(args))

    @server.tool()
    def escalate_to_human(
        reason: Annotated[str, Field(description="Why a person needs to follow up.")],
        urgency: Urgency = "routine",
        callback_number: str | None = None,
        patient_id: str | None = None,
    ) -> JSON:
        """Open a ticket for the clinic team to follow up."""
        args = EscalateArgs(
            reason=reason, urgency=urgency, callback_number=callback_number, patient_id=patient_id
        )
        return _run(lambda: tools.escalate_to_human(args))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CallPilot clinic tools as an MCP server.")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP, not stdio")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    settings = get_settings()
    tz = ZoneInfo(settings.clinic_timezone)
    conn = connect(settings.database_path)
    init_db(conn)
    seed_all(conn, now=datetime.now(UTC), tz=tz)
    tools = ClinicTools(ClinicRepository(conn, tz), clock=lambda: datetime.now(UTC))
    server = build_server(tools)
    if args.http:
        server.run("streamable-http", port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
