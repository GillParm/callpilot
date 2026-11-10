"""A scripted booking call shared by several tests."""

from __future__ import annotations

from collections.abc import Callable

from callpilot.providers.scripted import CallTool, Results, Say, Turn
from callpilot.session.call_session import CallSession
from tests.support import RecordingTransport


class SimulatedCaller:
    def __init__(self, session: CallSession, transport: RecordingTransport) -> None:
        self.session = session
        self.transport = transport

    async def say(self, text: str) -> None:
        done = len(self.transport.of_type("agent.done"))
        await self.session.caller_text(text)
        await self.transport.wait_for("agent.done", done + 1)


def first_slot(results: Results) -> str:
    return str(results["check_availability"]["slots"][0]["slot_id"])


def booking(confirmed: bool) -> Callable[[Results], dict[str, object]]:
    def build(results: Results) -> dict[str, object]:
        return {
            "patient_id": results["lookup_patient"]["patient_id"],
            "slot_id": first_slot(results),
            "reason": "cleaning",
            "caller_confirmed": confirmed,
        }

    return build


BOOKING_SCRIPT = [
    Turn(
        [
            Say("Happy to help. Let me check tomorrow morning."),
            CallTool(
                "check_availability",
                {
                    "appointment_type": "cleaning",
                    "date_from": "2026-11-11",
                    "time_of_day": "morning",
                },
            ),
            Say("I have nine o'clock or ten o'clock with Priya. Which suits you?"),
        ]
    ),
    Turn(
        [
            CallTool("lookup_patient", {"full_name": "Maria Lopez", "date_of_birth": "1988-03-14"}),
            CallTool("book_appointment", booking(confirmed=False)),
            Say("That's a cleaning for Maria Lopez tomorrow at nine with Priya. Shall I book it?"),
        ]
    ),
    Turn(
        [
            CallTool("book_appointment", booking(confirmed=True)),
            Say("You're all set for tomorrow at nine. Anything else?"),
        ]
    ),
    Turn([Say("Thanks for calling Brightside Dental. Goodbye!")]),
]
