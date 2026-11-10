"""Domain records returned by the repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Patient:
    id: str
    first_name: str
    last_name: str
    date_of_birth: date
    phone: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


@dataclass(frozen=True)
class Slot:
    id: str
    dentist_id: str
    dentist_name: str
    role: str
    starts_at: datetime  # aware, in the clinic's timezone
    duration_minutes: int
    is_free: bool


@dataclass(frozen=True)
class Appointment:
    id: str
    patient_id: str
    slot: Slot
    reason: str
    status: str
