import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from callpilot.agent.guardrails import GuardedToolExecutor, ToolOutcome
from callpilot.agent.state import ConversationState
from callpilot.clinic.repository import ClinicRepository
from callpilot.tools.clinic_tools import ClinicTools

MARIA = {"full_name": "Maria Lopez", "date_of_birth": "1988-03-14"}
JAMES = {"full_name": "James Carter", "date_of_birth": "1975-11-02"}


class Harness:
    """Drives the executor the way a conversation would."""

    def __init__(self, executor: GuardedToolExecutor, conn: sqlite3.Connection) -> None:
        self.executor = executor
        self.conn = conn
        self._n = 0

    @property
    def state(self) -> ConversationState:
        return self.executor.state

    def call(self, name: str, arguments: dict[str, Any] | str) -> ToolOutcome:
        self._n += 1
        return self.executor.execute(f"call-{self._n}", name, arguments)

    def caller_says_something(self) -> None:
        self.state.note_caller_turn()

    def first_offered_slot(self, appointment_type: str = "checkup") -> str:
        out = self.call(
            "check_availability", {"appointment_type": appointment_type, "date_from": "2026-11-11"}
        )
        return str(out.result["slots"][0]["slot_id"])

    def active_bookings(self, patient_id: str) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM appointments WHERE patient_id = ? AND status = 'booked'",
                (patient_id,),
            ).fetchone()[0]
        )


@pytest.fixture
def h(conn: sqlite3.Connection, tz: ZoneInfo, now: datetime) -> Harness:
    tools = ClinicTools(ClinicRepository(conn, tz), clock=lambda: now, call_id="call-x")
    return Harness(GuardedToolExecutor(tools, ConversationState(call_id="call-x")), conn)


def _book(slot_id: str, confirmed: bool, patient_id: str = "P-1002") -> dict[str, Any]:
    return {
        "patient_id": patient_id,
        "slot_id": slot_id,
        "reason": "checkup",
        "caller_confirmed": confirmed,
    }


# Happy path ---------------------------------------------------------------------


def test_two_step_booking(h: Harness) -> None:
    slot_id = h.first_offered_slot()
    h.call("lookup_patient", JAMES)

    first = h.call("book_appointment", _book(slot_id, confirmed=False))
    assert first.ok
    assert first.result["status"] == "confirmation_required"
    assert first.guardrail == "confirmation_required"
    assert "James Carter" in first.result["read_back"]
    assert "Wednesday, November 11" in first.result["read_back"]
    assert h.active_bookings("P-1002") == 0, "nothing is written before confirmation"

    h.caller_says_something()
    second = h.call("book_appointment", _book(slot_id, confirmed=True))
    assert second.ok
    assert second.result["status"] == "booked"
    assert h.active_bookings("P-1002") == 1
    assert h.state.outcome == "booked"


# Rules ---------------------------------------------------------------------------


def test_booking_requires_a_verified_patient(h: Harness) -> None:
    slot_id = h.first_offered_slot()
    out = h.call("book_appointment", _book(slot_id, confirmed=False))
    assert not out.ok
    assert out.guardrail == "patient_not_verified"


def test_cannot_book_for_someone_else(h: Harness) -> None:
    slot_id = h.first_offered_slot()
    h.call("lookup_patient", JAMES)
    out = h.call("book_appointment", _book(slot_id, confirmed=False, patient_id="P-1003"))
    assert out.guardrail == "patient_mismatch"


def test_cannot_book_a_slot_that_was_never_offered(h: Harness, conn: sqlite3.Connection) -> None:
    h.call("lookup_patient", JAMES)
    # A real, free slot, but the model made it up instead of asking the tool.
    real_free_slot = conn.execute(
        "SELECT id FROM slots WHERE id NOT IN (SELECT slot_id FROM appointments) LIMIT 1"
    ).fetchone()["id"]
    out = h.call("book_appointment", _book(real_free_slot, confirmed=False))
    assert out.guardrail == "slot_not_offered"


def test_confirmed_flag_on_first_call_does_not_skip_the_read_back(h: Harness) -> None:
    slot_id = h.first_offered_slot()
    h.call("lookup_patient", JAMES)
    out = h.call("book_appointment", _book(slot_id, confirmed=True))
    assert out.result["status"] == "confirmation_required"
    assert h.active_bookings("P-1002") == 0


def test_model_cannot_confirm_without_the_caller_speaking(h: Harness) -> None:
    slot_id = h.first_offered_slot()
    h.call("lookup_patient", JAMES)
    h.call("book_appointment", _book(slot_id, confirmed=False))
    out = h.call("book_appointment", _book(slot_id, confirmed=True))
    assert out.guardrail == "awaiting_caller"
    assert h.active_bookings("P-1002") == 0


def test_changing_the_slot_requires_a_new_read_back(h: Harness) -> None:
    out = h.call("check_availability", {"appointment_type": "checkup", "date_from": "2026-11-11"})
    first_slot, second_slot = (s["slot_id"] for s in out.result["slots"][:2])
    h.call("lookup_patient", JAMES)
    h.call("book_appointment", _book(first_slot, confirmed=False))
    h.caller_says_something()
    changed = h.call("book_appointment", _book(second_slot, confirmed=True))
    assert changed.result["status"] == "confirmation_required"
    assert h.active_bookings("P-1002") == 0


def test_too_many_failed_lookups_forces_escalation(h: Harness) -> None:
    wrong = {"full_name": "Maria Lopez", "date_of_birth": "1990-01-01"}
    for _ in range(3):
        assert h.call("lookup_patient", wrong).result["found"] is False
    blocked = h.call("lookup_patient", MARIA)
    assert blocked.guardrail == "too_many_lookups"
    assert "escalate_to_human" in blocked.result["error"]["message"]


# Cancelling and rescheduling ---------------------------------------------------


def test_cancel_requires_one_of_the_callers_own_appointments(h: Harness) -> None:
    h.call("lookup_patient", JAMES)
    out = h.call(
        "cancel_appointment",
        {"patient_id": "P-1002", "appointment_id": "A-SOMEONE-ELSE", "caller_confirmed": False},
    )
    assert out.guardrail == "unknown_appointment"


def test_reschedule_books_new_and_cancels_old(h: Harness) -> None:
    found = h.call("lookup_patient", MARIA).result
    old_id = found["upcoming_appointments"][0]["appointment_id"]
    new_slot = h.first_offered_slot("cleaning")

    book = {"patient_id": "P-1001", "slot_id": new_slot, "reason": "cleaning"}
    cancel = {"patient_id": "P-1001", "appointment_id": old_id}

    def status(name: str, args: dict[str, Any], confirmed: bool) -> str:
        return str(h.call(name, {**args, "caller_confirmed": confirmed}).result["status"])

    assert status("book_appointment", book, confirmed=False) == "confirmation_required"
    assert status("cancel_appointment", cancel, confirmed=False) == "confirmation_required"
    h.caller_says_something()
    assert status("book_appointment", book, confirmed=True) == "booked"
    assert status("cancel_appointment", cancel, confirmed=True) == "cancelled"
    assert h.state.outcome == "rescheduled"


# Escalation and malformed calls -----------------------------------------------


def test_escalation_attaches_the_verified_patient(h: Harness, conn: sqlite3.Connection) -> None:
    h.call("lookup_patient", MARIA)
    out = h.call("escalate_to_human", {"reason": "billing question"})
    assert out.ok
    row = conn.execute("SELECT patient_id FROM escalations").fetchone()
    assert row["patient_id"] == "P-1001"
    assert h.state.outcome == "escalated"


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("lookup_patient", "{not json", "invalid_json"),
        ("transfer_money", {}, "unknown_tool"),
        ("lookup_patient", {"full_name": "Maria Lopez"}, "invalid_arguments"),
        ("check_availability", {"appointment_type": "massage", "date_from": "2026-11-11"},
         "invalid_arguments"),
        ("check_availability", {"appointment_type": "checkup", "date_from": "2026-11-01"},
         "date_in_past"),
    ],
)  # fmt: skip
def test_bad_calls_return_structured_errors(
    h: Harness, name: str, arguments: dict[str, Any] | str, code: str
) -> None:
    out = h.call(name, arguments)
    assert not out.ok
    assert out.result["error"]["code"] == code


def test_arguments_may_arrive_as_a_json_string(h: Harness) -> None:
    out = h.call("lookup_patient", '{"full_name": "Maria Lopez", "date_of_birth": "1988-03-14"}')
    assert out.result["found"] is True
    assert out.duration_ms >= 0
