// Messages exchanged with the backend over /ws/call. Mirrors
// backend/src/callpilot/transports/browser.py.

export type Mode = "voice" | "text";
export type Role = "caller" | "agent";

export interface ToolResultEvent {
  type: "tool.result";
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  ok: boolean;
  guardrail: string | null;
  duration_ms: number;
}

export interface TurnMetricEvent {
  type: "metrics.turn";
  turn: number;
  kind: "time_to_first_audio" | "time_to_first_token";
  latency_ms: number;
  tool_ms: number;
  tools: string[];
  endpointing_ms?: number;
  response_ms?: number;
  overlapped?: boolean;
}

export type ServerEvent =
  | { type: "call.started"; call_id: string; mode: Mode; provider: string; model: string }
  | { type: "transcript"; role: Role; text: string; interrupted?: boolean }
  | { type: "transcript.delta"; role: Role; text: string }
  | { type: "tool.call"; id: string; name: string; arguments: Record<string, unknown> | string }
  | ToolResultEvent
  | TurnMetricEvent
  | { type: "barge_in"; trigger: string; audio_played_ms: number | null }
  | { type: "audio.clear" }
  | { type: "agent.response" }
  | { type: "agent.done"; interrupted: boolean }
  | { type: "error"; message: string; fatal?: boolean }
  | { type: "call.ended"; reason: string; outcome: string };

export type ClientMessage =
  | { type: "caller.text"; text: string }
  | { type: "playback"; played_ms: number }
  | { type: "client.latency"; turn: number; perceived_ms: number }
  | { type: "hangup" };

/** What the backend's /api/config returns. */
export interface BackendConfig {
  clinic: { name: string; address: string; phone: string; hours: string[] };
  providers: { voice: string | null; text: string | null };
  demo_patients: { name: string; date_of_birth: string }[];
}
