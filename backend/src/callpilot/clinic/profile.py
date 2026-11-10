"""Static facts about the (fictional) clinic the agent works for.

Everything here is made up. Keeping it in one place means the system prompt,
the seeder and the frontend all describe the same clinic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["dentist", "hygienist", "orthodontist"]
AppointmentType = Literal[
    "cleaning",
    "checkup",
    "filling",
    "toothache",
    "whitening",
    "orthodontic_consult",
]

# Which kind of provider handles each appointment type.
ROLE_FOR_TYPE: dict[AppointmentType, Role] = {
    "cleaning": "hygienist",
    "checkup": "dentist",
    "filling": "dentist",
    "toothache": "dentist",
    "whitening": "dentist",
    "orthodontic_consult": "orthodontist",
}

SLOT_MINUTES: dict[Role, int] = {"dentist": 30, "hygienist": 60, "orthodontist": 30}


@dataclass(frozen=True)
class StaffMember:
    id: str
    name: str
    role: Role
    # ISO weekdays they work: Monday=1 ... Sunday=7
    workdays: tuple[int, ...]


@dataclass(frozen=True)
class ClinicProfile:
    name: str
    address: str
    phone: str
    hours: tuple[str, ...]
    services: tuple[str, ...]
    policies: tuple[str, ...]
    staff: tuple[StaffMember, ...] = field(default_factory=tuple)


CLINIC = ClinicProfile(
    name="Brightside Dental",
    address="1420 Harbourview Avenue, Suite 210",
    phone="555-0142",
    hours=(
        "Monday to Friday, 9 AM to 5 PM (closed 12 to 1 for lunch)",
        "Saturday, 9 AM to 1 PM",
        "Closed Sunday",
    ),
    services=(
        "cleanings and hygiene visits",
        "checkups and exams",
        "fillings",
        "same-week toothache visits",
        "teeth whitening",
        "orthodontic consultations",
    ),
    policies=(
        "Please give at least 24 hours' notice to cancel or reschedule.",
        "New patients are welcome; bring photo ID and your insurance card.",
        "We bill most insurance plans directly.",
        "For a life-threatening emergency, call 911.",
    ),
    staff=(
        StaffMember("dr-okafor", "Dr. Amara Okafor", "dentist", (1, 2, 3, 4, 5)),
        StaffMember("dr-moreau", "Dr. Lucas Moreau", "dentist", (1, 3, 4, 6)),
        StaffMember("hyg-shah", "Priya Shah", "hygienist", (1, 2, 3, 4, 5, 6)),
        StaffMember("dr-vasquez", "Dr. Elena Vasquez", "orthodontist", (2, 4)),
    ),
)
