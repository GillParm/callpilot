"""Tool implementations over the clinic repository.

These functions know about the clinic, not about conversations: they validate
domain rules (the slot exists and is free, the appointment belongs to the
patient) and return JSON-friendly dicts. Conversation rules such as "confirm
before booking" live in `callpilot.agent.guardrails`.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

from callpilot.clinic.models import Appointment, Patient, Slot
from callpilot.clinic.profile import ROLE_FOR_TYPE
from callpilot.clinic.repository import (
    AppointmentNotFoundError,
    ClinicRepository,
    SlotUnavailableError,
)
from callpilot.clinic.timefmt import spoken_datetime
from callpilot.tools.schemas import (
    BookAppointmentArgs,
    CancelAppointmentArgs,
    CheckAvailabilityArgs,
    EscalateArgs,
    LookupPatientArgs,
)

MAX_SEARCH_DAYS = 14
MAX_SLOTS_RETURNED = 6
MAX_SLOTS_PER_DAY_IN_RANGE = 2
NAME_MATCH_THRESHOLD = 0.8
LATE_CANCEL_HOURS = 24

JSON = dict[str, Any]


class ToolError(Exception):
    """A tool failed for a reason the model should hear about and recover from."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _normalise_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z ]", " ", ascii_only.casefold()).split())


def name_similarity(spoken: str, patient: Patient) -> float:
    """How closely a (possibly mis-transcribed) spoken name matches a patient.

    Speech-to-text often mangles names, so the date of birth is the strict key
    and the name only has to be close. Both "first last" and "last first" orders
    are accepted.
    """
    query = _normalise_name(spoken)
    candidates = (
        _normalise_name(f"{patient.first_name} {patient.last_name}"),
        _normalise_name(f"{patient.last_name} {patient.first_name}"),
    )
    return max(difflib.SequenceMatcher(None, query, c).ratio() for c in candidates)


class ClinicTools:
    def __init__(
        self,
        repo: ClinicRepository,
        *,
        clock: Callable[[], datetime],
        call_id: str | None = None,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._call_id = call_id

    def _now(self) -> datetime:
        return self._clock().astimezone(self._repo.tz)

    # check_availability ------------------------------------------------------

    def check_availability(self, args: CheckAvailabilityArgs) -> JSON:
        now = self._now()
        today = now.date()
        if args.date_from < today:
            raise ToolError("date_in_past", f"{args.date_from} is in the past; today is {today}.")
        date_to = args.date_to or args.date_from
        if date_to < args.date_from:
            raise ToolError("bad_range", "date_to must be on or after date_from.")
        note = None
        if (date_to - args.date_from).days > MAX_SEARCH_DAYS:
            date_to = args.date_from + timedelta(days=MAX_SEARCH_DAYS)
            note = f"Search was limited to {MAX_SEARCH_DAYS} days, ending {date_to}."

        dentist_id = None
        if args.dentist_name:
            dentist_id = self._repo.find_dentist_id(args.dentist_name)
            if dentist_id is None:
                raise ToolError("unknown_provider", f"No provider named {args.dentist_name!r}.")

        tz = self._repo.tz
        start = max(datetime.combine(args.date_from, time(0), tzinfo=tz), now)
        end = datetime.combine(date_to + timedelta(days=1), time(0), tzinfo=tz)
        candidates = self._repo.free_slots(
            role=ROLE_FOR_TYPE[args.appointment_type],
            start=start,
            end=end,
            dentist_id=dentist_id,
            limit=200,
        )
        candidates = [s for s in candidates if _matches_time_of_day(s, args.time_of_day)]
        slots = _spread(candidates, single_day=args.date_from == date_to)

        result: JSON = {
            "appointment_type": args.appointment_type,
            "searched": {"from": args.date_from.isoformat(), "to": date_to.isoformat()},
            "slots": [_slot_view(s) for s in slots],
        }
        if not slots:
            result["message"] = "No openings match. Offer a different day or time of day."
        if note:
            result["note"] = note
        return result

    # lookup_patient ----------------------------------------------------------

    def lookup_patient(self, args: LookupPatientArgs) -> JSON:
        scored = [
            (name_similarity(args.full_name, p), p)
            for p in self._repo.patients_born_on(args.date_of_birth)
        ]
        scored = [(score, p) for score, p in scored if score >= NAME_MATCH_THRESHOLD]
        if not scored:
            return {
                "found": False,
                "message": "No patient matches that name and date of birth.",
            }
        _, patient = max(scored, key=lambda pair: pair[0])
        upcoming = self._repo.upcoming_appointments(patient.id, now=self._now())
        return {
            "found": True,
            "patient_id": patient.id,
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "upcoming_appointments": [_appointment_view(a) for a in upcoming],
        }

    # book_appointment --------------------------------------------------------

    def book_appointment(self, args: BookAppointmentArgs) -> JSON:
        if self._repo.get_patient(args.patient_id) is None:
            raise ToolError("unknown_patient", "Unknown patient_id. Look the caller up first.")
        slot = self._repo.get_slot(args.slot_id)
        if slot is None:
            raise ToolError("unknown_slot", "That slot_id does not exist.")
        if slot.starts_at <= self._now():
            raise ToolError("slot_in_past", "That slot has already started.")
        if not slot.is_free:
            raise ToolError("slot_taken", "That slot was just taken. Check availability again.")
        try:
            appointment = self._repo.book(
                patient_id=args.patient_id,
                slot_id=args.slot_id,
                reason=args.reason,
                now=self._now(),
                call_id=self._call_id,
            )
        except SlotUnavailableError as exc:
            raise ToolError(
                "slot_taken", "That slot was just taken. Check availability again."
            ) from exc
        return {"status": "booked", **_appointment_view(appointment)}

    # cancel_appointment ------------------------------------------------------

    def cancel_appointment(self, args: CancelAppointmentArgs) -> JSON:
        appointment = self._repo.get_appointment(args.appointment_id)
        if appointment is None or appointment.patient_id != args.patient_id:
            raise ToolError("unknown_appointment", "No such appointment for this patient.")
        if appointment.status != "booked":
            raise ToolError("not_active", "That appointment is already cancelled.")
        now = self._now()
        if appointment.slot.starts_at <= now:
            raise ToolError("in_past", "That appointment has already started.")
        try:
            cancelled = self._repo.cancel(appointment.id, now=now)
        except AppointmentNotFoundError as exc:
            raise ToolError("not_active", "That appointment is already cancelled.") from exc
        late = appointment.slot.starts_at - now < timedelta(hours=LATE_CANCEL_HOURS)
        return {"status": "cancelled", "late_cancellation": late, **_appointment_view(cancelled)}

    # escalate_to_human -------------------------------------------------------

    def escalate_to_human(self, args: EscalateArgs) -> JSON:
        ticket = self._repo.create_escalation(
            reason=args.reason,
            urgency=args.urgency,
            now=self._now(),
            call_id=self._call_id,
            patient_id=args.patient_id,
            callback_number=args.callback_number,
        )
        if args.urgency == "emergency":
            next_step = (
                "Tell the caller the on-call dentist has been paged and will call back within "
                "15 minutes, and that for anything life-threatening they should call 911 now."
            )
        else:
            next_step = "Tell the caller a team member will call them back today."
        return {"status": "escalated", "ticket_id": ticket, "next_step": next_step}


def _matches_time_of_day(slot: Slot, time_of_day: str) -> bool:
    if time_of_day == "morning":
        return slot.starts_at.hour < 12
    if time_of_day == "afternoon":
        return slot.starts_at.hour >= 12
    return True


def _spread(slots: list[Slot], *, single_day: bool) -> list[Slot]:
    """Pick a handful of options, spread over several days when searching a range."""
    if single_day:
        return slots[:MAX_SLOTS_RETURNED]
    picked: list[Slot] = []
    per_day: dict[str, int] = {}
    for slot in slots:
        day = slot.starts_at.date().isoformat()
        if per_day.get(day, 0) >= MAX_SLOTS_PER_DAY_IN_RANGE:
            continue
        per_day[day] = per_day.get(day, 0) + 1
        picked.append(slot)
        if len(picked) == MAX_SLOTS_RETURNED:
            break
    return picked


def _slot_view(slot: Slot) -> JSON:
    return {
        "slot_id": slot.id,
        "when": spoken_datetime(slot.starts_at),
        "starts_at": slot.starts_at.isoformat(),
        "provider": slot.dentist_name,
        "duration_minutes": slot.duration_minutes,
    }


def _appointment_view(appointment: Appointment) -> JSON:
    return {
        "appointment_id": appointment.id,
        "when": spoken_datetime(appointment.slot.starts_at),
        "starts_at": appointment.slot.starts_at.isoformat(),
        "provider": appointment.slot.dentist_name,
        "reason": appointment.reason,
        "duration_minutes": appointment.slot.duration_minutes,
    }
