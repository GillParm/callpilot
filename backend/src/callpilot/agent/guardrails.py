"""The layer between the model and the clinic tools.

Every tool call from any provider goes through `GuardedToolExecutor.execute`.
It validates arguments, enforces the conversation rules below, runs the tool,
and returns a JSON-friendly result plus enough metadata for the call log.

Rules enforced here (not just requested in the prompt):

* **No invented availability.** `book_appointment` only accepts a `slot_id`
  that `check_availability` returned earlier in this call.
* **Identity first.** Booking and cancelling require a patient verified with
  `lookup_patient` in this call, and only that patient's own appointments.
* **Confirm before writing.** Writes are two-step. The first call parks the
  request and returns a read-back. The second call (`caller_confirmed=true`)
  only goes through if the key arguments match and the caller has spoken since
  the read-back, so the model can't confirm on the caller's behalf.
* **No brute-forcing identities.** After three failed lookups the agent is
  told to hand the call to a human.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from callpilot.agent.state import ActionKind, ConversationState, PendingAction
from callpilot.tools.clinic_tools import ClinicTools, ToolError
from callpilot.tools.registry import TOOLS_BY_NAME
from callpilot.tools.schemas import (
    AgentBookAppointmentArgs,
    AgentCancelAppointmentArgs,
    BookAppointmentArgs,
    CancelAppointmentArgs,
    CheckAvailabilityArgs,
    EscalateArgs,
    LookupPatientArgs,
)

MAX_FAILED_LOOKUPS = 3

JSON = dict[str, Any]


@dataclass
class ToolOutcome:
    """The result of one tool call, as recorded in the call log."""

    call_id: str
    name: str
    arguments: JSON
    result: JSON  # exactly what the model receives
    guardrail: str | None = None  # set when a rule blocked or parked the call
    duration_ms: float = 0.0
    extra: JSON = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.result.get("ok"))


class GuardrailViolation(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _ParkedForConfirmation(Exception):
    def __init__(self, read_back: str) -> None:
        super().__init__(read_back)
        self.read_back = read_back


class GuardedToolExecutor:
    def __init__(self, tools: ClinicTools, state: ConversationState) -> None:
        self._tools = tools
        self._state = state

    @property
    def state(self) -> ConversationState:
        return self._state

    def execute(self, call_id: str, name: str, raw_arguments: JSON | str) -> ToolOutcome:
        started = time.perf_counter()
        outcome = self._execute(call_id, name, raw_arguments)
        outcome.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return outcome

    def _execute(self, call_id: str, name: str, raw_arguments: JSON | str) -> ToolOutcome:
        arguments = _parse_arguments(raw_arguments)
        if arguments is None:
            return _error(call_id, name, {}, "invalid_json", "Arguments were not valid JSON.")

        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            return _error(call_id, name, arguments, "unknown_tool", f"There is no tool {name!r}.")

        try:
            args = spec.args_model.model_validate(arguments)
        except ValidationError as exc:
            return _error(call_id, name, arguments, "invalid_arguments", _summarise(exc))

        try:
            result = self._dispatch(args)
        except _ParkedForConfirmation as parked:
            result = {
                "status": "confirmation_required",
                "read_back": parked.read_back,
                "instruction": (
                    "Nothing has been saved yet. Read this back to the caller and ask them to "
                    "confirm. If they say yes, call again with caller_confirmed=true and the "
                    "same arguments."
                ),
            }
        except GuardrailViolation as violation:
            outcome = _error(call_id, name, arguments, violation.code, violation.message)
            outcome.guardrail = violation.code
            return outcome
        except ToolError as error:
            return _error(call_id, name, arguments, error.code, error.message)

        outcome = ToolOutcome(call_id, name, arguments, {"ok": True, **result})
        if result.get("status") == "confirmation_required":
            outcome.guardrail = "confirmation_required"
        return outcome

    # Dispatch ----------------------------------------------------------------

    def _dispatch(self, args: BaseModel) -> JSON:
        match args:
            case CheckAvailabilityArgs():
                return self._check_availability(args)
            case LookupPatientArgs():
                return self._lookup_patient(args)
            case AgentBookAppointmentArgs():
                return self._book(args)
            case AgentCancelAppointmentArgs():
                return self._cancel(args)
            case EscalateArgs():
                return self._escalate(args)
        raise AssertionError(f"no handler for {type(args).__name__}")

    def _check_availability(self, args: CheckAvailabilityArgs) -> JSON:
        result = self._tools.check_availability(args)
        for slot in result["slots"]:
            self._state.offered_slots[slot["slot_id"]] = slot
        return result

    def _lookup_patient(self, args: LookupPatientArgs) -> JSON:
        if self._state.failed_lookups >= MAX_FAILED_LOOKUPS:
            raise GuardrailViolation(
                "too_many_lookups",
                "Too many failed lookups on this call. Offer to transfer the caller to the "
                "team with escalate_to_human instead of trying again.",
            )
        result = self._tools.lookup_patient(args)
        if not result["found"]:
            self._state.failed_lookups += 1
            return result
        self._state.verified_patient_id = result["patient_id"]
        self._state.verified_patient_name = f"{result['first_name']} {result['last_name']}"
        self._state.known_appointments = {
            a["appointment_id"]: a for a in result["upcoming_appointments"]
        }
        return result

    def _book(self, args: AgentBookAppointmentArgs) -> JSON:
        self._require_verified(args.patient_id)
        slot = self._state.offered_slots.get(args.slot_id)
        if slot is None:
            raise GuardrailViolation(
                "slot_not_offered",
                "That slot_id did not come from check_availability on this call. Only "
                "offer and book times that check_availability returned.",
            )
        read_back = (
            f"{args.reason} for {self._state.verified_patient_name} on {slot['when']} "
            f"with {slot['provider']}"
        )
        self._require_confirmation("book", (args.patient_id, args.slot_id), args, read_back)

        result = self._tools.book_appointment(
            BookAppointmentArgs(
                patient_id=args.patient_id, slot_id=args.slot_id, reason=args.reason
            )
        )
        self._state.offered_slots.pop(args.slot_id, None)
        self._state.known_appointments[result["appointment_id"]] = result
        self._state.outcomes.append("booked")
        return result

    def _cancel(self, args: AgentCancelAppointmentArgs) -> JSON:
        self._require_verified(args.patient_id)
        appointment = self._state.known_appointments.get(args.appointment_id)
        if appointment is None:
            raise GuardrailViolation(
                "unknown_appointment",
                "That appointment_id is not one of this caller's upcoming appointments.",
            )
        read_back = (
            f"cancel the {appointment['reason']} on {appointment['when']} "
            f"with {appointment['provider']}"
        )
        self._require_confirmation(
            "cancel", (args.patient_id, args.appointment_id), args, read_back
        )

        result = self._tools.cancel_appointment(
            CancelAppointmentArgs(patient_id=args.patient_id, appointment_id=args.appointment_id)
        )
        self._state.known_appointments.pop(args.appointment_id, None)
        self._state.outcomes.append("cancelled")
        return result

    def _escalate(self, args: EscalateArgs) -> JSON:
        if args.patient_id is None and self._state.verified_patient_id:
            args = args.model_copy(update={"patient_id": self._state.verified_patient_id})
        result = self._tools.escalate_to_human(args)
        self._state.outcomes.append("escalated")
        return result

    # Rules -------------------------------------------------------------------

    def _require_verified(self, patient_id: str) -> None:
        if self._state.verified_patient_id is None:
            raise GuardrailViolation(
                "patient_not_verified",
                "Verify the caller with lookup_patient (full name and date of birth) first.",
            )
        if patient_id != self._state.verified_patient_id:
            raise GuardrailViolation(
                "patient_mismatch",
                "You can only act on the verified caller's own record.",
            )

    def _require_confirmation(
        self,
        kind: ActionKind,
        key: tuple[str, ...],
        args: AgentBookAppointmentArgs | AgentCancelAppointmentArgs,
        read_back: str,
    ) -> None:
        """Return normally only when this write has been confirmed by the caller.

        Otherwise park it and raise `_ParkedForConfirmation`, which `_execute`
        turns into a `confirmation_required` result for the model.
        """
        pending = self._state.pending.get(kind)
        confirmed = (
            args.caller_confirmed
            and pending is not None
            and pending.key == key
            and self._state.caller_turns > pending.parked_at_caller_turn
        )
        if confirmed:
            del self._state.pending[kind]
            return

        if args.caller_confirmed and pending is not None and pending.key == key:
            raise GuardrailViolation(
                "awaiting_caller",
                "The caller has not answered the read-back yet. Ask them to confirm and "
                "wait for their reply before calling again.",
            )

        self._state.pending[kind] = PendingAction(
            kind=kind,
            key=key,
            read_back=read_back,
            parked_at_caller_turn=self._state.caller_turns,
        )
        raise _ParkedForConfirmation(read_back)


def _parse_arguments(raw: JSON | str) -> JSON | None:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _summarise(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        location = ".".join(str(p) for p in err["loc"]) or "arguments"
        parts.append(f"{location}: {err['msg']}")
    return "; ".join(parts)


def _error(call_id: str, name: str, arguments: JSON, code: str, message: str) -> ToolOutcome:
    return ToolOutcome(
        call_id, name, arguments, {"ok": False, "error": {"code": code, "message": message}}
    )
