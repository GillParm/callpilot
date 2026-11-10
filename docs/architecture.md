# CallPilot architecture

CallPilot is a voice receptionist for **Brightside Dental**, a fictional clinic.
A caller talks to it from the browser (or a phone, through Twilio), and it can
look up patients, find free slots, book or cancel appointments and hand the
call to a human. The goal of the project is to show the engineering that a
production voice agent needs: low latency, barge-in, tool calling with
guardrails, and enough observability to know why a call went the way it did.

## Components

```
browser (Next.js) ── WS: PCM16 24 kHz ──┐
                                        ├──► CallSession ──► RealtimeProvider ──► OpenAI Realtime / Gemini Live
Twilio phone ─── WS: mu-law 8 kHz ──────┘        │                              (text mode: Claude / OpenAI)
                                                 ├──► ToolExecutor + guardrails ──► SQLite (clinic data)
                                                 └──► CallRecorder ──► call log, latency stats, LLM summary

MCP server (stdio / streamable HTTP) ──► the same clinic tools
```

| Path | What lives there |
| --- | --- |
| `backend/src/callpilot/db` | SQLite schema, connection helpers and the fake-data seeder |
| `backend/src/callpilot/clinic` | Domain models and the repository that queries the clinic DB |
| `backend/src/callpilot/tools` | The five agent tools, their argument schemas and a registry that renders them for each provider |
| `backend/src/callpilot/agent` | System prompt, per-call conversation state and the guardrails that sit between the model and the tools |
| `backend/src/callpilot/providers` | `RealtimeProvider` interface plus OpenAI Realtime, Gemini Live, Claude (text) and a deterministic scripted provider for tests |
| `backend/src/callpilot/session` | `CallSession`: the orchestrator that wires a transport to a provider, runs tools, handles barge-in and measures latency |
| `backend/src/callpilot/transports` | Browser WebSocket and Twilio Media Streams adapters |
| `backend/src/callpilot/calllog` | Persistence for calls, transcripts, tool calls and latency, plus summaries |
| `backend/src/callpilot/mcp_server.py` | MCP server exposing the clinic tools to other agents |
| `frontend/` | Next.js app: live call console and the call log |
| `infra/` | Terraform for Cloud Run, Artifact Registry and Secret Manager |

## Audio format

Inside the backend every stream is **PCM16, mono, 24 kHz**. Each edge converts
to and from that:

- The browser captures at 24 kHz in an AudioWorklet, so no conversion is needed.
- Twilio sends 8 kHz mu-law; the Twilio transport decodes and upsamples on the
  way in, and downsamples and encodes on the way out.
- Providers that want something else convert internally (Gemini Live accepts
  any input rate if it is declared in the MIME type).

## Guardrails live in code, not just the prompt

The system prompt keeps the agent on clinic topics, but the rules that matter
are enforced by `agent/guardrails.py`, so a model that ignores its prompt still
can't do damage:

1. **No invented availability.** `book_appointment` only accepts a slot ID that
   `check_availability` returned earlier in the same call.
2. **Identity first.** Booking or cancelling requires a patient verified with
   `lookup_patient` (name + date of birth) in the same call.
3. **Confirm before writing.** Booking and cancelling are two-step. The first
   call returns a read-back summary and parks the request; the second call is
   only accepted after the caller has spoken again and the arguments match
   what was read back.

Rejected calls return a structured error that tells the model what to do next,
and every rejection is recorded in the call log.

## Barge-in

1. The provider's voice activity detection reports that the caller started
   speaking.
2. `CallSession` cancels the in-flight response and tells the transport to drop
   any queued audio (`clear` to the browser, the `clear` event to Twilio).
3. The browser reports how many milliseconds of the response were actually
   played, and the session truncates the assistant's turn to that point, so the
   model's memory of what it said matches what the caller heard.

## Latency

Every turn records **time to first audio (TTFA)**: from the last voiced audio
frame the caller sent to the first audio frame of the reply leaving the server.
The browser also measures perceived latency (local end of speech to the first
sample played) and reports it back, so both numbers end up in the call log.

## Milestones

1. Plan and scaffold
2. Text-mode agent with tools and tests
3. Real-time audio in the browser
4. Dashboard and call log
5. Twilio and MCP server
6. Deployment and README
