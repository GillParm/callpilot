"""Measure real voice latency with a synthetic caller.

Plays a scripted caller into a real voice session (the configured voice
provider, real tools, in-memory database) in real time, the way a phone would:
20 ms audio frames, silence between sentences, and a client that "plays" agent
audio at real speed and reports playback progress. Then it prints per-turn
time-to-first-audio and writes a JSON report.

Caller lines are synthesised with macOS `say` by default, or read from WAV
files (PCM16 mono 24 kHz) with --wav-dir.

    uv run python scripts/benchmark_voice.py --runs 3
    uv run python scripts/benchmark_voice.py --barge-in   # also time an interruption
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from callpilot.config import Settings
from callpilot.db import connect, init_db
from callpilot.db.seed import seed_all
from callpilot.providers.factory import create_provider, resolve_provider_name
from callpilot.session.factory import build_session

FRAME_MS = 20
FRAME_BYTES = 24_000 * 2 * FRAME_MS // 1000
SILENCE = b"\x00" * FRAME_BYTES

CALLER_LINES = (
    "Hi, I'd like to book a teeth cleaning, sometime next week in the morning.",
    "The first one works for me.",
    "My name is Maria Lopez, and my date of birth is March fourteenth, nineteen eighty eight.",
    "Yes, please book it.",
    "No, that's everything. Thanks, bye!",
)


def synthesise(lines: tuple[str, ...], out_dir: Path) -> list[bytes]:
    clips = []
    for index, line in enumerate(lines):
        aiff = out_dir / f"{index}.aiff"
        wav = out_dir / f"{index}.wav"
        subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), line], check=True)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(aiff), str(wav)],
            check=True,
        )
        clips.append(read_wav(wav))
    return clips


def read_wav(path: Path) -> bytes:
    with wave.open(str(path)) as wav:
        if wav.getframerate() != 24_000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected PCM16 mono 24 kHz")
        return wav.readframes(wav.getnframes())


class PlaybackClient:
    """Pretends to be a browser: plays agent audio at real speed."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.queued_ms = 0.0
        self.played_ms = 0.0
        self.first_audio_at: float | None = None
        self.changed = asyncio.Event()

    async def send_event(self, event: dict[str, Any]) -> None:
        self.events.append({**event, "_t": time.monotonic()})
        if event["type"] == "agent.response":
            self.played_ms = self.queued_ms = 0.0
        self.changed.set()

    async def send_audio(self, pcm: bytes) -> None:
        if self.first_audio_at is None:
            self.first_audio_at = time.monotonic()
        self.queued_ms += len(pcm) / 48

    async def clear_audio(self) -> None:
        self.queued_ms = self.played_ms

    def tick(self, ms: float) -> None:
        self.played_ms = min(self.queued_ms, self.played_ms + ms)

    @property
    def speaking(self) -> bool:
        return self.played_ms < self.queued_ms

    def count(self, event_type: str) -> int:
        return sum(1 for e in self.events if e["type"] == event_type)


async def run_call(settings: Settings, clips: list[bytes], *, barge_in: bool) -> dict[str, Any]:
    tz = ZoneInfo(settings.clinic_timezone)
    db = connect(":memory:")
    init_db(db)
    seed_all(db, now=datetime.now(UTC), tz=tz)
    client = PlaybackClient()
    session, config = build_session(
        conn=db,
        tz=tz,
        provider=create_provider(settings, "voice"),
        transport=client,
        mode="voice",
        channel="benchmark",
        clock=lambda: datetime.now(UTC),
    )
    await session.start(config)

    async def stream(pcm: bytes) -> None:
        for offset in range(0, len(pcm), FRAME_BYTES):
            await session.caller_audio(pcm[offset : offset + FRAME_BYTES].ljust(FRAME_BYTES, b"\0"))
            client.tick(FRAME_MS)
            session.playback_progress(int(client.played_ms))
            await asyncio.sleep(FRAME_MS / 1000)

    async def silence_until_agent_finishes(limit_s: float = 20.0) -> None:
        deadline = time.monotonic() + limit_s
        heard_agent = False
        quiet_frames = 0
        while time.monotonic() < deadline:
            await stream(SILENCE)
            heard_agent = heard_agent or client.speaking
            quiet_frames = 0 if (client.speaking or session._responding) else quiet_frames + 1
            if heard_agent and quiet_frames * FRAME_MS >= 700:
                return

    await silence_until_agent_finishes()  # the greeting
    interruption: dict[str, Any] | None = None
    queue = list(clips)
    while queue:
        await stream(queue.pop(0))
        if barge_in and interruption is None and queue:
            # Let the agent talk for a second, then talk over it with the next line.
            while not client.speaking:
                await stream(SILENCE)
            await stream(SILENCE * 50)
            before = client.count("barge_in")
            started = time.monotonic()
            line = queue.pop(0)
            for offset in range(0, len(line), FRAME_BYTES):
                await stream(line[offset : offset + FRAME_BYTES])
                if interruption is None and client.count("barge_in") > before:
                    interruption = {"reaction_ms": round((time.monotonic() - started) * 1000)}
        await silence_until_agent_finishes()
    await session.hang_up()

    metrics = [e for e in client.events if e["type"] == "metrics.turn"]
    tools = [(e["name"], e["guardrail"]) for e in client.events if e["type"] == "tool.result"]
    transcript = [
        f"{e['role'].upper():6} {e['text']}" for e in client.events if e["type"] == "transcript"
    ]
    booked = db.execute("SELECT COUNT(*) FROM appointments WHERE source_call_id IS NOT NULL")
    return {
        "provider": session.provider.name,
        "model": session.provider.model,
        "turns": [
            {
                k: m.get(k)
                for k in (
                    "turn",
                    "latency_ms",
                    "endpointing_ms",
                    "response_ms",
                    "tools",
                    "overlapped",
                )
            }
            for m in metrics
        ],
        "tools": tools,
        "booked": booked.fetchone()[0],
        "interruption": interruption,
        "transcript": transcript,
    }


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--barge-in", action="store_true")
    parser.add_argument("--wav-dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("../docs/benchmarks/latest.json"))
    args = parser.parse_args()

    settings = Settings()
    if resolve_provider_name(settings, "voice") is None:
        raise SystemExit("No voice provider configured: set GEMINI_API_KEY or OPENAI_API_KEY.")

    with tempfile.TemporaryDirectory() as tmp:
        if args.wav_dir:
            clips = [read_wav(p) for p in sorted(args.wav_dir.glob("*.wav"))]
        else:
            clips = synthesise(CALLER_LINES, Path(tmp))

    runs = []
    for run in range(args.runs):
        print(f"run {run + 1}/{args.runs}...", file=sys.stderr)
        runs.append(await run_call(settings, clips, barge_in=args.barge_in))

    # Overlapped turns (the caller was still talking) are not response times.
    measured = [t for r in runs for t in r["turns"] if not t.get("overlapped")]
    latencies = [t["latency_ms"] for t in measured]
    with_tools = [t["latency_ms"] for t in measured if t["tools"]]
    without_tools = [t["latency_ms"] for t in measured if not t["tools"]]
    endpointing = [t["endpointing_ms"] for t in measured if t.get("endpointing_ms") is not None]
    summary = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": runs[0]["provider"],
        "model": runs[0]["model"],
        "runs": args.runs,
        "turns_measured": len(latencies),
        "ttfa_ms": {
            "p50": round(statistics.median(latencies)),
            "p90": round(percentile(latencies, 0.9)),
            "min": min(latencies),
            "max": max(latencies),
        }
        if latencies
        else None,
        "ttfa_without_tools_p50_ms": round(statistics.median(without_tools))
        if without_tools
        else None,
        "ttfa_with_tools_p50_ms": round(statistics.median(with_tools)) if with_tools else None,
        "endpointing_p50_ms": round(statistics.median(endpointing)) if endpointing else None,
        "bookings": sum(r["booked"] for r in runs),
        "interruptions": [r["interruption"] for r in runs if r["interruption"]],
        "calls": runs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n")
    for line in runs[-1]["transcript"]:
        print(line)
    print(json.dumps({k: v for k, v in summary.items() if k != "calls"}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
