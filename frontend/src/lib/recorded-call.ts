// A recorded call from the call log, replayed into the same shapes the live
// console renders, so both views share the timeline, transcript and charts.

import type {
  LatencyMark,
  Segment,
  SegmentTone,
  ToolActivity,
  TranscriptLine,
  TurnStats,
} from "./call-state";
import type { Mode, Role } from "./protocol";

export interface CallStats {
  turns: number;
  latency_p50_ms: number | null;
  latency_p90_ms: number | null;
  barge_ins: number;
  tool_calls: number;
  guardrail_blocks: number;
}

export interface CallRecord {
  id: string;
  channel: string;
  mode: Mode;
  provider: string;
  model: string;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  outcome: string | null;
  summary: string | null;
  caller_number: string | null;
  stats: CallStats;
}

export interface RecordedEvent {
  at: string;
  elapsed_ms: number;
  type: string;
  // Event payloads are JSON written by the backend; each branch below reads the fields it needs.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: any;
}

export interface CallDetail extends CallRecord {
  events: RecordedEvent[];
}

export interface ReplayedCall {
  transcript: TranscriptLine[];
  tools: ToolActivity[];
  turns: TurnStats[];
  segments: Segment[];
  latency: LatencyMark[];
  bargeIns: number[];
  durationMs: number;
}

const tone = (payload: { ok: boolean; guardrail: string | null }): SegmentTone =>
  payload.guardrail === "confirmation_required" ? "confirm" : payload.ok ? "ok" : "blocked";

export function replay(call: CallDetail): ReplayedCall {
  const result: ReplayedCall = {
    transcript: [],
    tools: [],
    turns: [],
    segments: [],
    latency: [],
    bargeIns: [],
    durationMs: 0,
  };
  for (const event of call.events) {
    const at = event.elapsed_ms;
    const p = event.payload;
    result.durationMs = Math.max(result.durationMs, at);
    switch (event.type) {
      case "transcript":
        result.transcript.push({
          id: result.transcript.length,
          role: p.role as Role,
          text: p.text,
          streaming: false,
          interrupted: Boolean(p.interrupted),
          at,
        });
        break;
      case "tool_call": {
        // Recorded when the tool finished; it started duration_ms earlier.
        const start = Math.max(0, at - Math.round(p.duration_ms));
        result.tools.push({
          id: p.id,
          name: p.name,
          arguments: p.arguments,
          at: start,
          result: p.result,
          ok: p.ok,
          guardrail: p.guardrail,
          durationMs: p.duration_ms,
        });
        result.segments.push({
          id: `tool-${p.id}`,
          lane: "tool",
          start,
          end: at,
          label: p.name,
          tone: tone(p),
        });
        break;
      }
      case "segment":
        result.segments.push({
          id: `${p.lane}-${p.start_ms}-${result.segments.length}`,
          lane: p.lane,
          start: p.start_ms,
          end: Math.max(p.end_ms, p.start_ms + 250),
        });
        result.durationMs = Math.max(result.durationMs, p.end_ms);
        break;
      case "latency":
        result.turns.push({
          turn: p.turn,
          kind: p.kind,
          latencyMs: p.latency_ms,
          endpointingMs: p.endpointing_ms,
          responseMs: p.response_ms,
          tools: p.tools ?? [],
          overlapped: Boolean(p.overlapped),
        });
        result.latency.push({ turn: p.turn, at, latencyMs: p.latency_ms, overlapped: Boolean(p.overlapped) });
        break;
      case "client_latency":
        result.turns = result.turns.map((t) =>
          t.turn === p.turn ? { ...t, perceivedMs: p.perceived_ms } : t,
        );
        break;
      case "barge_in":
        result.bargeIns.push(at);
        break;
    }
  }
  return result;
}
