"""Reading the call log back: lists, details and per-call stats."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

from callpilot.clinic.timefmt import from_utc_iso

JSON = dict[str, Any]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _duration_s(row: sqlite3.Row) -> float | None:
    if not row["ended_at"]:
        return None
    started: datetime = from_utc_iso(row["started_at"])
    ended: datetime = from_utc_iso(row["ended_at"])
    return round((ended - started).total_seconds(), 1)


def call_stats(events: list[JSON]) -> JSON:
    """Headline numbers for one call, computed from its event stream."""
    latencies = [
        e["payload"]["latency_ms"]
        for e in events
        if e["type"] == "latency" and not e["payload"].get("overlapped")
    ]
    tool_calls = [e["payload"] for e in events if e["type"] == "tool_call"]
    p50 = percentile(latencies, 0.5)
    p90 = percentile(latencies, 0.9)
    return {
        "turns": sum(1 for e in events if e["type"] == "latency"),
        "latency_p50_ms": None if p50 is None else round(p50),
        "latency_p90_ms": None if p90 is None else round(p90),
        "barge_ins": sum(1 for e in events if e["type"] == "barge_in"),
        "tool_calls": len(tool_calls),
        "guardrail_blocks": sum(
            1
            for t in tool_calls
            if t.get("guardrail") and t["guardrail"] != "confirmation_required"
        ),
    }


def _events_for(conn: sqlite3.Connection, call_ids: list[str]) -> dict[str, list[JSON]]:
    grouped: dict[str, list[JSON]] = defaultdict(list)
    if not call_ids:
        return grouped
    placeholders = ",".join("?" for _ in call_ids)
    rows = conn.execute(
        f"SELECT call_id, at, elapsed_ms, type, payload FROM call_events "
        f"WHERE call_id IN ({placeholders}) ORDER BY id",
        call_ids,
    )
    for row in rows:
        grouped[row["call_id"]].append(
            {
                "at": row["at"],
                "elapsed_ms": row["elapsed_ms"],
                "type": row["type"],
                "payload": json.loads(row["payload"]),
            }
        )
    return grouped


def _call_summary(row: sqlite3.Row, events: list[JSON]) -> JSON:
    return {
        "id": row["id"],
        "channel": row["channel"],
        "mode": row["mode"],
        "provider": row["provider"],
        "model": row["model"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_s": _duration_s(row),
        "outcome": row["outcome"],
        "summary": row["summary"],
        "caller_number": row["caller_number"],
        "stats": call_stats(events),
    }


def list_calls(conn: sqlite3.Connection, *, limit: int = 50) -> list[JSON]:
    rows = conn.execute(
        "SELECT * FROM calls ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)
    ).fetchall()
    events = _events_for(conn, [row["id"] for row in rows])
    return [_call_summary(row, events[row["id"]]) for row in rows]


def get_call(conn: sqlite3.Connection, call_id: str) -> JSON | None:
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if row is None:
        return None
    events = _events_for(conn, [call_id])[call_id]
    return {**_call_summary(row, events), "events": events}


def transcript_text(events: list[JSON]) -> str:
    """The conversation as plain text, one line per utterance (for summaries)."""
    lines = []
    for event in events:
        if event["type"] != "transcript":
            continue
        speaker = "Caller" if event["payload"]["role"] == "caller" else "Receptionist"
        suffix = " [cut off]" if event["payload"].get("interrupted") else ""
        lines.append(f"{speaker}: {event['payload']['text']}{suffix}")
    for event in events:
        if event["type"] == "tool_call":
            payload = event["payload"]
            status = payload["result"].get("status") or ("ok" if payload["ok"] else "error")
            lines.append(f"[tool] {payload['name']} -> {status}")
    return "\n".join(lines)
