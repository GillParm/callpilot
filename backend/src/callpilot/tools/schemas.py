"""Tool argument models.

These Pydantic models are the single source of truth for every tool: they
validate arguments coming back from a model and render the JSON Schema that
each provider (and the MCP server) advertises.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from callpilot.clinic.profile import AppointmentType

TimeOfDay = Literal["any", "morning", "afternoon"]
Urgency = Literal["routine", "urgent", "emergency"]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CheckAvailabilityArgs(_Args):
    appointment_type: AppointmentType = Field(
        description="What the visit is for. Use 'toothache' for pain or a broken tooth."
    )
    date_from: date = Field(description="First day to search, YYYY-MM-DD, clinic local time.")
    date_to: date | None = Field(
        default=None,
        description="Last day to search, inclusive. Defaults to date_from. Max 14 days later.",
    )
    time_of_day: TimeOfDay = Field(
        default="any", description="Morning is before noon, afternoon is noon or later."
    )
    dentist_name: str | None = Field(
        default=None, description="Only if the caller asks for a specific provider."
    )


class LookupPatientArgs(_Args):
    full_name: str = Field(min_length=2, description="Caller's first and last name.")
    date_of_birth: date = Field(description="Caller's date of birth, YYYY-MM-DD.")


class BookAppointmentArgs(_Args):
    patient_id: str = Field(description="patient_id returned by lookup_patient.")
    slot_id: str = Field(description="slot_id returned by check_availability.")
    reason: str = Field(min_length=2, max_length=200, description="Short reason for the visit.")


class CancelAppointmentArgs(_Args):
    patient_id: str = Field(description="patient_id returned by lookup_patient.")
    appointment_id: str = Field(description="appointment_id returned by lookup_patient.")


class EscalateArgs(_Args):
    reason: str = Field(min_length=2, max_length=500, description="Why a human is needed.")
    urgency: Urgency = Field(
        default="routine",
        description="'emergency' for severe pain, swelling, bleeding or trauma.",
    )
    callback_number: str | None = Field(
        default=None, description="Number to call back, if the caller gave one."
    )
    patient_id: str | None = Field(
        default=None, description="patient_id, if the caller has been verified."
    )


# The voice agent books and cancels in two steps (see agent/guardrails.py).
# These variants add the flag that drives that flow; MCP clients use the
# plain versions above.

_CONFIRM_DESCRIPTION = (
    "false on the first call, which returns a read-back. true only after reading it "
    "back and hearing the caller say yes."
)


class AgentBookAppointmentArgs(BookAppointmentArgs):
    caller_confirmed: bool = Field(default=False, description=_CONFIRM_DESCRIPTION)


class AgentCancelAppointmentArgs(CancelAppointmentArgs):
    caller_confirmed: bool = Field(default=False, description=_CONFIRM_DESCRIPTION)
