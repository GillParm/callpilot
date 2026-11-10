import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from callpilot.clinic.models import Patient
from callpilot.clinic.repository import ClinicRepository
from callpilot.tools.clinic_tools import ClinicTools, ToolError, name_similarity
from callpilot.tools.schemas import (
    BookAppointmentArgs,
    CancelAppointmentArgs,
    CheckAvailabilityArgs,
    EscalateArgs,
    LookupPatientArgs,
)

WEDNESDAY = date(2026, 11, 11)


@pytest.fixture
def tools(conn: sqlite3.Connection, tz: ZoneInfo, now: datetime) -> ClinicTools:
    return ClinicTools(ClinicRepository(conn, tz), clock=lambda: now, call_id="call-test")


def _check(tools: ClinicTools, **kwargs: object) -> dict[str, object]:
    return tools.check_availability(CheckAvailabilityArgs.model_validate(kwargs))


# check_availability ----------------------------------------------------------


def test_single_day_search_returns_slots_on_that_day(tools: ClinicTools) -> None:
    result = _check(tools, appointment_type="cleaning", date_from=WEDNESDAY)
    slots = result["slots"]
    assert isinstance(slots, list)
    assert 0 < len(slots) <= 6
    assert all(s["starts_at"].startswith("2026-11-11") for s in slots)
    assert all(s["provider"] == "Priya Shah" for s in slots)


def test_range_search_spreads_options_over_days(tools: ClinicTools) -> None:
    result = _check(
        tools, appointment_type="checkup", date_from=WEDNESDAY, date_to=date(2026, 11, 20)
    )
    days = [s["starts_at"][:10] for s in result["slots"]]
    assert len(days) == 6
    assert max(days.count(d) for d in set(days)) <= 2


def test_time_of_day_filter(tools: ClinicTools) -> None:
    result = _check(tools, appointment_type="checkup", date_from=WEDNESDAY, time_of_day="afternoon")
    hours = [int(s["starts_at"][11:13]) for s in result["slots"]]
    assert hours
    assert all(h >= 12 for h in hours)


def test_specific_provider(tools: ClinicTools) -> None:
    result = _check(
        tools,
        appointment_type="checkup",
        date_from=WEDNESDAY,
        date_to=date(2026, 11, 13),
        dentist_name="Dr. Moreau",
    )
    assert {s["provider"] for s in result["slots"]} == {"Dr. Lucas Moreau"}


def test_no_openings_on_sunday_says_so(tools: ClinicTools) -> None:
    result = _check(tools, appointment_type="checkup", date_from=date(2026, 11, 15))
    assert result["slots"] == []
    assert "message" in result


def test_past_dates_are_rejected(tools: ClinicTools) -> None:
    with pytest.raises(ToolError) as exc:
        _check(tools, appointment_type="checkup", date_from=date(2026, 11, 1))
    assert exc.value.code == "date_in_past"


def test_long_ranges_are_clamped_with_a_note(tools: ClinicTools) -> None:
    result = _check(
        tools, appointment_type="checkup", date_from=WEDNESDAY, date_to=date(2027, 1, 30)
    )
    assert "note" in result
    assert result["searched"] == {"from": "2026-11-11", "to": "2026-11-25"}


def test_unknown_provider_is_an_error(tools: ClinicTools) -> None:
    with pytest.raises(ToolError) as exc:
        _check(tools, appointment_type="checkup", date_from=WEDNESDAY, dentist_name="Dr. Nobody")
    assert exc.value.code == "unknown_provider"


# lookup_patient -----------------------------------------------------------------


def test_lookup_finds_patient_with_upcoming_appointments(tools: ClinicTools) -> None:
    result = tools.lookup_patient(
        LookupPatientArgs(full_name="Maria Lopez", date_of_birth=date(1988, 3, 14))
    )
    assert result["found"] is True
    assert result["patient_id"] == "P-1001"
    assert len(result["upcoming_appointments"]) == 1


@pytest.mark.parametrize("spoken", ["maria lopez", "Lopez Maria", "Mariah Lopes", "María López"])
def test_lookup_tolerates_transcription_noise(tools: ClinicTools, spoken: str) -> None:
    result = tools.lookup_patient(
        LookupPatientArgs(full_name=spoken, date_of_birth=date(1988, 3, 14))
    )
    assert result["found"] is True


def test_lookup_requires_matching_date_of_birth(tools: ClinicTools) -> None:
    result = tools.lookup_patient(
        LookupPatientArgs(full_name="Maria Lopez", date_of_birth=date(1988, 3, 15))
    )
    assert result == {"found": False, "message": "No patient matches that name and date of birth."}


def test_lookup_rejects_a_different_name_with_same_birthday(tools: ClinicTools) -> None:
    result = tools.lookup_patient(
        LookupPatientArgs(full_name="Robert Smith", date_of_birth=date(1988, 3, 14))
    )
    assert result["found"] is False


def test_name_similarity_orders() -> None:
    patient = Patient("P", "Aisha", "Rahman", date(1992, 7, 21), "555")
    assert name_similarity("Rahman Aisha", patient) == 1.0
    assert name_similarity("Ayesha Rahman", patient) > 0.8
    assert name_similarity("John Doe", patient) < 0.5


# book_appointment / cancel_appointment ---------------------------------------------


def _first_free_slot(tools: ClinicTools) -> str:
    result = _check(tools, appointment_type="checkup", date_from=WEDNESDAY)
    return str(result["slots"][0]["slot_id"])


def test_book_appointment(tools: ClinicTools) -> None:
    slot_id = _first_free_slot(tools)
    result = tools.book_appointment(
        BookAppointmentArgs(patient_id="P-1002", slot_id=slot_id, reason="checkup")
    )
    assert result["status"] == "booked"
    assert result["when"].startswith("Wednesday, November 11")
    again = _check(tools, appointment_type="checkup", date_from=WEDNESDAY)
    assert slot_id not in {s["slot_id"] for s in again["slots"]}


def test_booking_the_same_slot_twice_fails_cleanly(tools: ClinicTools) -> None:
    slot_id = _first_free_slot(tools)
    args = BookAppointmentArgs(patient_id="P-1002", slot_id=slot_id, reason="checkup")
    tools.book_appointment(args)
    with pytest.raises(ToolError) as exc:
        tools.book_appointment(args)
    assert exc.value.code == "slot_taken"


@pytest.mark.parametrize(
    ("patient_id", "slot_id", "code"),
    [("P-9999", None, "unknown_patient"), ("P-1002", "S-made-up", "unknown_slot")],
)
def test_book_validates_ids(
    tools: ClinicTools, patient_id: str, slot_id: str | None, code: str
) -> None:
    args = BookAppointmentArgs(
        patient_id=patient_id, slot_id=slot_id or _first_free_slot(tools), reason="checkup"
    )
    with pytest.raises(ToolError) as exc:
        tools.book_appointment(args)
    assert exc.value.code == code


def test_cancel_appointment(tools: ClinicTools) -> None:
    found = tools.lookup_patient(
        LookupPatientArgs(full_name="Maria Lopez", date_of_birth=date(1988, 3, 14))
    )
    appointment_id = found["upcoming_appointments"][0]["appointment_id"]
    result = tools.cancel_appointment(
        CancelAppointmentArgs(patient_id="P-1001", appointment_id=appointment_id)
    )
    assert result["status"] == "cancelled"
    assert result["late_cancellation"] is False


def test_cannot_cancel_someone_elses_appointment(tools: ClinicTools) -> None:
    found = tools.lookup_patient(
        LookupPatientArgs(full_name="Maria Lopez", date_of_birth=date(1988, 3, 14))
    )
    appointment_id = found["upcoming_appointments"][0]["appointment_id"]
    with pytest.raises(ToolError) as exc:
        tools.cancel_appointment(
            CancelAppointmentArgs(patient_id="P-1002", appointment_id=appointment_id)
        )
    assert exc.value.code == "unknown_appointment"


def test_late_cancellation_is_flagged(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime
) -> None:
    repo = ClinicRepository(conn, tz)
    slot = repo.free_slots(role="dentist", start=now, end=now + timedelta(days=1))[0]
    appointment = repo.book(patient_id="P-1003", slot_id=slot.id, reason="filling", now=now)
    tools = ClinicTools(repo, clock=lambda: now)
    result = tools.cancel_appointment(
        CancelAppointmentArgs(patient_id="P-1003", appointment_id=appointment.id)
    )
    assert result["late_cancellation"] is True


# escalate_to_human -------------------------------------------------------------------


def test_escalation_emergency_mentions_911(tools: ClinicTools) -> None:
    result = tools.escalate_to_human(
        EscalateArgs(reason="face swelling after extraction", urgency="emergency")
    )
    assert result["status"] == "escalated"
    assert "911" in result["next_step"]
