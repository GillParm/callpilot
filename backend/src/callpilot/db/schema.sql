-- Clinic data ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dentists (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('dentist', 'hygienist', 'orthodontist'))
);

CREATE TABLE IF NOT EXISTS patients (
    id              TEXT PRIMARY KEY,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    date_of_birth   TEXT NOT NULL,          -- ISO date
    phone           TEXT NOT NULL,
    email           TEXT
);
CREATE INDEX IF NOT EXISTS patients_by_dob ON patients (date_of_birth);

-- A bookable block of time for one provider. Availability is derived:
-- a slot is free when it has no active appointment.
CREATE TABLE IF NOT EXISTS slots (
    id                  TEXT PRIMARY KEY,
    dentist_id          TEXT NOT NULL REFERENCES dentists (id),
    starts_at           TEXT NOT NULL,      -- UTC, ISO 8601 with 'Z'
    duration_minutes    INTEGER NOT NULL,
    UNIQUE (dentist_id, starts_at)
);
CREATE INDEX IF NOT EXISTS slots_by_start ON slots (starts_at);

CREATE TABLE IF NOT EXISTS appointments (
    id              TEXT PRIMARY KEY,
    patient_id      TEXT NOT NULL REFERENCES patients (id),
    slot_id         TEXT NOT NULL REFERENCES slots (id),
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('booked', 'cancelled')),
    created_at      TEXT NOT NULL,
    cancelled_at    TEXT,
    source_call_id  TEXT
);
-- The database itself refuses double-booking, whatever the application does.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_slot
    ON appointments (slot_id) WHERE status = 'booked';
CREATE INDEX IF NOT EXISTS appointments_by_patient ON appointments (patient_id);

CREATE TABLE IF NOT EXISTS escalations (
    id              TEXT PRIMARY KEY,
    call_id         TEXT,
    patient_id      TEXT,
    reason          TEXT NOT NULL,
    urgency         TEXT NOT NULL CHECK (urgency IN ('routine', 'urgent', 'emergency')),
    callback_number TEXT,
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
);

-- Call log ------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS calls (
    id              TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,          -- browser | phone
    mode            TEXT NOT NULL,          -- voice | text
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    outcome         TEXT,                   -- booked | cancelled | escalated | info | abandoned
    summary         TEXT,
    caller_number   TEXT
);
CREATE INDEX IF NOT EXISTS calls_by_start ON calls (started_at DESC);

-- Append-only event stream for a call: transcripts, tool calls, guardrail
-- decisions, barge-ins and per-turn latency all land here.
CREATE TABLE IF NOT EXISTS call_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id     TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    at          TEXT NOT NULL,
    elapsed_ms  INTEGER NOT NULL,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL               -- JSON
);
CREATE INDEX IF NOT EXISTS call_events_by_call ON call_events (call_id, id);
