// Everything the console shows about the current call, derived from server
// events plus a few local signals (mic activity, playback start/stop).
// Times are milliseconds since the call started.

import type { Mode, Role, ServerEvent, ToolResultEvent, TurnMetricEvent } from "./protocol";

export type Lane = "caller" | "agent" | "tool";
export type SegmentTone = "ok" | "confirm" | "blocked";

export interface Segment {
  id: string;
  lane: Lane;
  start: number;
  end: number | null; // null while still open
  label?: string;
  tone?: SegmentTone;
}

export interface LatencyMark {
  turn: number;
  at: number; // when the agent's first audio/token arrived
  latencyMs: number;
  overlapped: boolean;
}

export interface TranscriptLine {
  id: number;
  role: Role;
  text: string;
  streaming: boolean;
  interrupted: boolean;
  at: number;
}

export interface ToolActivity {
  id: string;
  name: string;
  arguments: Record<string, unknown> | string;
  at: number;
  result?: Record<string, unknown>;
  ok?: boolean;
  guardrail?: string | null;
  durationMs?: number;
}

export interface TurnStats {
  turn: number;
  kind: TurnMetricEvent["kind"];
  latencyMs: number;
  endpointingMs?: number;
  responseMs?: number;
  tools: string[];
  overlapped: boolean;
  perceivedMs?: number;
}

export type CallStatus = "idle" | "connecting" | "live" | "ended" | "failed";

export interface CallState {
  status: CallStatus;
  mode: Mode;
  callId: string | null;
  provider: string | null;
  model: string | null;
  transcript: TranscriptLine[];
  tools: ToolActivity[];
  turns: TurnStats[];
  segments: Segment[];
  latency: LatencyMark[];
  bargeIns: number[];
  agentSpeaking: boolean;
  outcome: string | null;
  error: string | null;
}

export const initialCallState = (mode: Mode = "voice"): CallState => ({
  status: "idle",
  mode,
  callId: null,
  provider: null,
  model: null,
  transcript: [],
  tools: [],
  turns: [],
  segments: [],
  latency: [],
  bargeIns: [],
  agentSpeaking: false,
  outcome: null,
  error: null,
});

export type CallAction =
  | { type: "connect"; mode: Mode }
  | { type: "server"; event: ServerEvent; at: number }
  | { type: "caller.speech"; speaking: boolean; at: number }
  | { type: "agent.audio"; playing: boolean; at: number }
  | { type: "perceived"; turn: number; perceivedMs: number }
  | { type: "failed"; message: string };

const TOOL_TONE = (result: ToolResultEvent): SegmentTone =>
  result.guardrail === "confirmation_required" ? "confirm" : result.ok ? "ok" : "blocked";

function openSegment(state: CallState, lane: Lane, at: number, extra: Partial<Segment> = {}): Segment[] {
  const open = state.segments.find((s) => s.lane === lane && s.end === null && !extra.id);
  if (open) return state.segments;
  return [...state.segments, { id: extra.id ?? `${lane}-${at}`, lane, start: at, end: null, ...extra }];
}

function closeSegment(
  segments: Segment[],
  match: (s: Segment) => boolean,
  at: number,
  extra: Partial<Segment> = {},
): Segment[] {
  return segments.map((s) =>
    s.end === null && match(s) ? { ...s, end: Math.max(at, s.start), ...extra } : s,
  );
}

function closeAll(segments: Segment[], at: number): Segment[] {
  return segments.map((s) => (s.end === null ? { ...s, end: Math.max(at, s.start) } : s));
}

function applyServerEvent(state: CallState, event: ServerEvent, at: number): CallState {
  switch (event.type) {
    case "call.started":
      return {
        ...state,
        status: "live",
        callId: event.call_id,
        provider: event.provider,
        model: event.model,
      };

    case "transcript.delta": {
      const last = state.transcript.at(-1);
      if (last && last.role === event.role && last.streaming) {
        const updated = { ...last, text: last.text + event.text };
        return { ...state, transcript: [...state.transcript.slice(0, -1), updated] };
      }
      const line: TranscriptLine = {
        id: state.transcript.length,
        role: event.role,
        text: event.text,
        streaming: true,
        interrupted: false,
        at,
      };
      return { ...state, transcript: [...state.transcript, line] };
    }

    case "transcript": {
      const last = state.transcript.at(-1);
      const interrupted = Boolean(event.interrupted);
      if (last && last.role === event.role && last.streaming) {
        const updated = { ...last, text: event.text || last.text, streaming: false, interrupted };
        return { ...state, transcript: [...state.transcript.slice(0, -1), updated] };
      }
      const line: TranscriptLine = {
        id: state.transcript.length,
        role: event.role,
        text: event.text,
        streaming: false,
        interrupted,
        at,
      };
      let segments = state.segments;
      if (state.mode === "text" && event.role === "caller") {
        // Typed messages have no duration; show them as a short tick.
        segments = [...segments, { id: `caller-${at}`, lane: "caller", start: at, end: at + 250 }];
      }
      return { ...state, transcript: [...state.transcript, line], segments };
    }

    case "tool.call":
      return {
        ...state,
        tools: [...state.tools, { id: event.id, name: event.name, arguments: event.arguments, at }],
        segments: openSegment(state, "tool", at, { id: `tool-${event.id}`, label: event.name }),
      };

    case "tool.result":
      return {
        ...state,
        tools: state.tools.map((t) =>
          t.id === event.id
            ? {
                ...t,
                result: event.result,
                ok: event.ok,
                guardrail: event.guardrail,
                durationMs: event.duration_ms,
              }
            : t,
        ),
        segments: closeSegment(state.segments, (s) => s.id === `tool-${event.id}`, at, {
          tone: TOOL_TONE(event),
        }),
      };

    case "metrics.turn":
      return {
        ...state,
        turns: [
          ...state.turns,
          {
            turn: event.turn,
            kind: event.kind,
            latencyMs: event.latency_ms,
            endpointingMs: event.endpointing_ms,
            responseMs: event.response_ms,
            tools: event.tools,
            overlapped: Boolean(event.overlapped),
          },
        ],
        latency: [
          ...state.latency,
          { turn: event.turn, at, latencyMs: event.latency_ms, overlapped: Boolean(event.overlapped) },
        ],
      };

    case "barge_in":
      return { ...state, bargeIns: [...state.bargeIns, at] };

    case "agent.response":
      // In text mode there is no playback, so the response itself is the agent's "speech".
      return state.mode === "text" ? { ...state, segments: openSegment(state, "agent", at) } : state;

    case "agent.done":
      return state.mode === "text"
        ? { ...state, segments: closeSegment(state.segments, (s) => s.lane === "agent", at) }
        : state;

    case "error":
      return event.fatal
        ? { ...state, status: "failed", error: event.message }
        : { ...state, error: event.message };

    case "call.ended":
      return {
        ...state,
        status: state.status === "failed" ? "failed" : "ended",
        outcome: event.outcome,
        agentSpeaking: false,
        segments: closeAll(state.segments, at),
      };

    case "audio.clear":
      return state;
  }
}

export function callReducer(state: CallState, action: CallAction): CallState {
  switch (action.type) {
    case "connect":
      return { ...initialCallState(action.mode), status: "connecting" };
    case "server":
      return applyServerEvent(state, action.event, action.at);
    case "caller.speech":
      return {
        ...state,
        segments: action.speaking
          ? openSegment(state, "caller", action.at)
          : closeSegment(state.segments, (s) => s.lane === "caller", action.at),
      };
    case "agent.audio":
      return {
        ...state,
        agentSpeaking: action.playing,
        segments: action.playing
          ? openSegment(state, "agent", action.at)
          : closeSegment(state.segments, (s) => s.lane === "agent", action.at),
      };
    case "perceived":
      return {
        ...state,
        turns: state.turns.map((t) =>
          t.turn === action.turn ? { ...t, perceivedMs: action.perceivedMs } : t,
        ),
      };
    case "failed":
      return { ...state, status: "failed", error: action.message, agentSpeaking: false };
  }
}
