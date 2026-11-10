"""What the agent has established so far in one call.

The guardrails read this to decide whether a tool call is allowed: a slot can
only be booked if it was offered, a patient must be verified before their
appointments are touched, and writes need a confirmation the caller actually
gave.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ActionKind = Literal["book", "cancel"]


@dataclass
class PendingAction:
    """A write that has been read back to the caller and awaits their yes."""

    kind: ActionKind
    key: tuple[str, ...]  # the arguments that must not change between the two calls
    read_back: str
    parked_at_caller_turn: int


@dataclass
class ConversationState:
    call_id: str
    caller_turns: int = 0
    verified_patient_id: str | None = None
    verified_patient_name: str | None = None
    known_appointments: dict[str, dict[str, Any]] = field(default_factory=dict)
    offered_slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: dict[ActionKind, PendingAction] = field(default_factory=dict)
    failed_lookups: int = 0
    outcomes: list[str] = field(default_factory=list)

    def note_caller_turn(self) -> None:
        """Called once per caller utterance (a text message or a stretch of speech)."""
        self.caller_turns += 1

    @property
    def outcome(self) -> str:
        """One word for the call log: what did this call achieve?"""
        done = set(self.outcomes)
        if "escalated" in done:
            return "escalated"
        if {"booked", "cancelled"} <= done:
            return "rescheduled"
        if "booked" in done:
            return "booked"
        if "cancelled" in done:
            return "cancelled"
        return "info"
