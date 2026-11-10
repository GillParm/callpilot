import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from callpilot.clinic.repository import (
    AppointmentNotFoundError,
    ClinicRepository,
    SlotUnavailableError,
)


@pytest.fixture
def repo(conn: sqlite3.Connection, tz: ZoneInfo) -> ClinicRepository:
    return ClinicRepository(conn, tz)


def test_patients_born_on(repo: ClinicRepository) -> None:
    [maria] = repo.patients_born_on(date(1988, 3, 14))
    assert maria.full_name == "Maria Lopez"


def test_free_slots_are_free_ordered_and_in_range(repo: ClinicRepository, now: datetime) -> None:
    end = now + timedelta(days=3)
    slots = repo.free_slots(role="hygienist", start=now, end=end)
    assert slots
    assert all(s.is_free and s.role == "hygienist" for s in slots)
    assert all(now <= s.starts_at < end for s in slots)
    assert [s.starts_at for s in slots] == sorted(s.starts_at for s in slots)
    assert slots[0].starts_at.tzinfo == repo.tz


def test_free_slots_can_filter_by_dentist(repo: ClinicRepository, now: datetime) -> None:
    slots = repo.free_slots(
        role="dentist", start=now, end=now + timedelta(days=7), dentist_id="dr-moreau"
    )
    assert slots
    assert {s.dentist_id for s in slots} == {"dr-moreau"}


def test_find_dentist_id_matches_spoken_names(repo: ClinicRepository) -> None:
    assert repo.find_dentist_id("Dr. Okafor") == "dr-okafor"
    assert repo.find_dentist_id("priya") == "hyg-shah"
    assert repo.find_dentist_id("Dr. Who") is None


def test_book_then_slot_is_no_longer_free(repo: ClinicRepository, now: datetime) -> None:
    slot = repo.free_slots(role="dentist", start=now, end=now + timedelta(days=7))[0]
    appointment = repo.book(patient_id="P-1002", slot_id=slot.id, reason="checkup", now=now)
    assert appointment.status == "booked"
    refreshed = repo.get_slot(slot.id)
    assert refreshed is not None
    assert not refreshed.is_free
    assert appointment in repo.upcoming_appointments("P-1002", now=now)


def test_booking_a_taken_slot_raises(repo: ClinicRepository, now: datetime) -> None:
    slot = repo.free_slots(role="dentist", start=now, end=now + timedelta(days=7))[0]
    repo.book(patient_id="P-1002", slot_id=slot.id, reason="checkup", now=now)
    with pytest.raises(SlotUnavailableError):
        repo.book(patient_id="P-1003", slot_id=slot.id, reason="checkup", now=now)


def test_booking_an_unknown_slot_raises(repo: ClinicRepository, now: datetime) -> None:
    with pytest.raises(SlotUnavailableError):
        repo.book(patient_id="P-1002", slot_id="S-nope", reason="checkup", now=now)


def test_cancel_frees_the_slot(repo: ClinicRepository, now: datetime) -> None:
    [appointment] = repo.upcoming_appointments("P-1001", now=now)
    cancelled = repo.cancel(appointment.id, now=now)
    assert cancelled.status == "cancelled"
    slot = repo.get_slot(appointment.slot.id)
    assert slot is not None
    assert slot.is_free
    with pytest.raises(AppointmentNotFoundError):
        repo.cancel(appointment.id, now=now)


def test_create_escalation_returns_ticket(repo: ClinicRepository, now: datetime) -> None:
    ticket = repo.create_escalation(reason="billing question", urgency="routine", now=now)
    assert ticket.startswith("E-")
