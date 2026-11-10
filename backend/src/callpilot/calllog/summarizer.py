"""After-call notes for clinic staff, written by an LLM.

The summary runs in the background once a call ends, so it never adds
latency to the call itself. Claude is used when a key is set, then Gemini;
without either, a plain summary is assembled from the call's own data so the
call log is never empty.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any, Protocol

from callpilot.calllog.store import get_call, transcript_text
from callpilot.config import Settings

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
You write after-call notes for the front desk of a dental clinic. You get the
transcript of a call handled by the clinic's AI receptionist, plus the tools it
used. Write two or three plain sentences for the staff: who called (first name
if known), what they wanted, what was actually done (bookings, cancellations or
hand-offs, with days and times), and anything staff need to follow up on.
Only state what the transcript and tool results show. No preamble, lists or
markdown."""

OUTCOME_PHRASES = {
    "booked": "An appointment was booked.",
    "cancelled": "An appointment was cancelled.",
    "rescheduled": "An appointment was moved.",
    "escalated": "The call was handed to the clinic team for follow-up.",
    "info": "No changes were made to the schedule.",
}


class Summarizer(Protocol):
    name: str

    async def summarize(self, transcript: str, outcome: str) -> str: ...


class PlainSummarizer:
    """No model: a factual line from the call's outcome and first request."""

    name = "plain"

    async def summarize(self, transcript: str, outcome: str) -> str:
        first_request = next(
            (
                line.removeprefix("Caller: ")
                for line in transcript.splitlines()
                if line.startswith("Caller: ")
            ),
            None,
        )
        opening = (
            f'The caller said: "{first_request}"' if first_request else "The caller didn't speak."
        )
        return f"{opening} {OUTCOME_PHRASES.get(outcome, '')}".strip()


class AnthropicSummarizer:
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str, client: Any = None) -> None:
        import anthropic

        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def summarize(self, transcript: str, outcome: str) -> str:
        response = await self._client.beta.messages.create(
            model=self._model,
            max_tokens=2048,
            system=INSTRUCTIONS,
            messages=[{"role": "user", "content": f"Recorded outcome: {outcome}\n\n{transcript}"}],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("summary request was declined")
        from anthropic.types.beta import BetaTextBlock

        return "".join(b.text for b in response.content if isinstance(b, BetaTextBlock)).strip()


class GeminiSummarizer:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def summarize(self, transcript: str, outcome: str) -> str:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=f"Recorded outcome: {outcome}\n\n{transcript}",
            config=types.GenerateContentConfig(
                system_instruction=INSTRUCTIONS, max_output_tokens=400
            ),
        )
        return (response.text or "").strip()


def create_summarizer(settings: Settings) -> Summarizer:
    choice = settings.summary_provider
    anthropic_key = (
        settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    )
    gemini_key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    if choice in ("auto", "anthropic") and anthropic_key:
        return AnthropicSummarizer(api_key=anthropic_key, model=settings.anthropic_model)
    if choice in ("auto", "gemini") and gemini_key:
        return GeminiSummarizer(api_key=gemini_key, model=settings.gemini_text_model)
    return PlainSummarizer()


async def summarize_call(
    conn: sqlite3.Connection, call_id: str, summarizer: Summarizer, *, timeout_s: float = 60
) -> str | None:
    """Write the summary for a finished call. Falls back to the plain summary on any failure."""
    call = get_call(conn, call_id)
    if call is None:
        return None
    transcript = transcript_text(call["events"])
    outcome = call["outcome"] or "info"
    if not transcript.strip():
        summary = "The call ended before anything was said."
    else:
        try:
            async with asyncio.timeout(timeout_s):
                summary = await summarizer.summarize(transcript, outcome)
        except Exception as exc:
            log.warning("summary with %s failed: %s", summarizer.name, exc)
            summary = ""
        if not summary:
            summary = await PlainSummarizer().summarize(transcript, outcome)
    conn.execute("UPDATE calls SET summary = ? WHERE id = ?", (summary, call_id))
    return summary
