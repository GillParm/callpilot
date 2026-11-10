"""Data access for the clinic. All SQL lives here."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from callpilot.clinic.models import Appointment, Patient, Slot
from callpilot.clinic.timefmt import from_utc_iso, to_utc_iso


class SlotUnavailableError(Exception):
    """The slot was taken (or never existed) by the time we tried to book it."""


class AppointmentNotFoundError(Exception):
    pass


_SLOT_COLUMNS = """
    s.id, s.dentist_id, d.name AS dentist_name, d.role, s.starts_at, s.duration_minutes,
    NOT EXISTS (
        SELECT 1 FROM appointments a WHERE a.slot_id = s.id AND a.status = 'booked'
    ) AS is_free
"""


class ClinicRepository:
    def __init__(self, conn: sqlite3.Connection, tz: ZoneInfo) -> None:
        self._conn = conn
        self._tz = tz

    @property
    def tz(self) -> ZoneInfo:
        return self._tz

    # Patients ---------------------------------------------------------------

    def patients_born_on(self, dob: date) -> list[Patient]:
        rows = self._conn.execute(
            "SELECT * FROM patients WHERE date_of_birth = ? ORDER BY id", (dob.isoformat(),)
        ).fetchall()
        return [self._patient(row) for row in rows]

    def get_patient(self, patient_id: str) -> Patient | None:
        row = self._conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        return self._patient(row) if row else None

    # Slots ------------------------------------------------------------------

    def free_slots(
        self,
        *,
        role: str,
        start: datetime,
        end: datetime,
        dentist_id: str | None = None,
        limit: int = 50,
    ) -> list[Slot]:
        """Free slots for a provider role with `start <= starts_at < end`, earliest first."""
        sql = f"""
            SELECT {_SLOT_COLUMNS}
            FROM slots s JOIN dentists d ON d.id = s.dentist_id
            WHERE d.role = ? AND s.starts_at >= ? AND s.starts_at < ?
              AND (? IS NULL OR s.dentist_id = ?)
              AND NOT EXISTS (
                SELECT 1 FROM appointments a WHERE a.slot_id = s.id AND a.status = 'booked'
              )
            ORDER BY s.starts_at, s.dentist_id
            LIMIT ?
        """
        rows = self._conn.execute(
            sql, (role, to_utc_iso(start), to_utc_iso(end), dentist_id, dentist_id, limit)
        ).fetchall()
        return [self._slot(row) for row in rows]

    def get_slot(self, slot_id: str) -> Slot | None:
        row = self._conn.execute(
            f"SELECT {_SLOT_COLUMNS} FROM slots s JOIN dentists d ON d.id = s.dentist_id "
            "WHERE s.id = ?",
            (slot_id,),
        ).fetchone()
        return self._slot(row) if row else None

    def find_dentist_id(self, name: str) -> str | None:
        """Match a spoken provider name loosely: 'Dr. Okafor', 'okafor', 'Priya'."""
        needle = name.lower().replace("dr.", "").replace("dr ", "").strip()
        if not needle:
            return None
        for row in self._conn.execute("SELECT id, name FROM dentists ORDER BY id"):
            if needle in row["name"].lower():
                return str(row["id"])
        return None

    # Appointments -----------------------------------------------------------

    def book(
        self,
        *,
        patient_id: str,
        slot_id: str,
        reason: str,
        now: datetime,
        call_id: str | None = None,
    ) -> Appointment:
        appointment_id = f"A-{uuid.uuid4().hex[:8].upper()}"
        try:
            self._conn.execute(
                "INSERT INTO appointments "
                "(id, patient_id, slot_id, reason, status, created_at, source_call_id) "
                "VALUES (?, ?, ?, ?, 'booked', ?, ?)",
                (appointment_id, patient_id, slot_id, reason, to_utc_iso(now), call_id),
            )
        except sqlite3.IntegrityError as exc:
            # Unknown slot (foreign key) or someone else booked it first (unique index).
            raise SlotUnavailableError(slot_id) from exc
        appointment = self.get_appointment(appointment_id)
        assert appointment is not None
        return appointment

    def cancel(self, appointment_id: str, *, now: datetime) -> Appointment:
        cursor = self._conn.execute(
            "UPDATE appointments SET status = 'cancelled', cancelled_at = ? "
            "WHERE id = ? AND status = 'booked'",
            (to_utc_iso(now), appointment_id),
        )
        if cursor.rowcount != 1:
            raise AppointmentNotFoundError(appointment_id)
        appointment = self.get_appointment(appointment_id)
        assert appointment is not None
        return appointment

    def get_appointment(self, appointment_id: str) -> Appointment | None:
        row = self._conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
        ).fetchone()
        return self._appointment(row) if row else None

    def upcoming_appointments(self, patient_id: str, *, now: datetime) -> list[Appointment]:
        rows = self._conn.execute(
            """
            SELECT a.* FROM appointments a JOIN slots s ON s.id = a.slot_id
            WHERE a.patient_id = ? AND a.status = 'booked' AND s.starts_at >= ?
            ORDER BY s.starts_at
            """,
            (patient_id, to_utc_iso(now)),
        ).fetchall()
        return [self._appointment(row) for row in rows]

    # Escalations ------------------------------------------------------------

    def create_escalation(
        self,
        *,
        reason: str,
        urgency: str,
        now: datetime,
        call_id: str | None = None,
        patient_id: str | None = None,
        callback_number: str | None = None,
    ) -> str:
        ticket_id = f"E-{uuid.uuid4().hex[:6].upper()}"
        self._conn.execute(
            "INSERT INTO escalations "
            "(id, call_id, patient_id, reason, urgency, callback_number, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, call_id, patient_id, reason, urgency, callback_number, to_utc_iso(now)),
        )
        return ticket_id

    # Row mapping ------------------------------------------------------------

    @staticmethod
    def _patient(row: sqlite3.Row) -> Patient:
        return Patient(
            id=row["id"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            date_of_birth=date.fromisoformat(row["date_of_birth"]),
            phone=row["phone"],
        )

    def _slot(self, row: sqlite3.Row) -> Slot:
        return Slot(
            id=row["id"],
            dentist_id=row["dentist_id"],
            dentist_name=row["dentist_name"],
            role=row["role"],
            starts_at=from_utc_iso(row["starts_at"]).astimezone(self._tz),
            duration_minutes=row["duration_minutes"],
            is_free=bool(row["is_free"]),
        )

    def _appointment(self, row: sqlite3.Row) -> Appointment:
        slot = self.get_slot(row["slot_id"])
        assert slot is not None, "appointments always reference an existing slot"
        return Appointment(
            id=row["id"],
            patient_id=row["patient_id"],
            slot=slot,
            reason=row["reason"],
            status=row["status"],
        )
