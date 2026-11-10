import { describe, expect, it } from "vitest";

import { callReducer, initialCallState, type CallAction, type CallState } from "./call-state";
import type { ServerEvent } from "./protocol";

const server = (event: ServerEvent, at = 0): CallAction => ({ type: "server", event, at });
const run = (actions: CallAction[], start: CallState = initialCallState("voice")) =>
  actions.reduce(callReducer, start);

describe("callReducer", () => {
  it("goes live when the call starts", () => {
    const state = run([
      { type: "connect", mode: "voice" },
      server({ type: "call.started", call_id: "c1", mode: "voice", provider: "gemini", model: "m" }),
    ]);
    expect(state.status).toBe("live");
    expect(state.provider).toBe("gemini");
  });

  it("streams agent deltas into one line and finalises it", () => {
    const state = run([
      server({ type: "transcript.delta", role: "agent", text: "Thanks for " }),
      server({ type: "transcript.delta", role: "agent", text: "calling." }),
      server({ type: "transcript", role: "agent", text: "Thanks for calling." }),
    ]);
    expect(state.transcript).toHaveLength(1);
    expect(state.transcript[0]).toMatchObject({ text: "Thanks for calling.", streaming: false });
  });

  it("marks a line cut off by the caller as interrupted", () => {
    const state = run([
      server({ type: "transcript.delta", role: "agent", text: "We have openings on" }),
      server({ type: "transcript", role: "agent", text: "We have openings on", interrupted: true }),
    ]);
    expect(state.transcript[0].interrupted).toBe(true);
  });

  it("pairs tool calls with results and colours them by guardrail outcome", () => {
    const state = run([
      server({ type: "tool.call", id: "t1", name: "book_appointment", arguments: {} }, 100),
      server(
        {
          type: "tool.result",
          id: "t1",
          name: "book_appointment",
          arguments: {},
          result: { ok: true, status: "confirmation_required" },
          ok: true,
          guardrail: "confirmation_required",
          duration_ms: 1.2,
        },
        104,
      ),
    ]);
    expect(state.tools[0]).toMatchObject({ guardrail: "confirmation_required", durationMs: 1.2 });
    expect(state.segments[0]).toMatchObject({ lane: "tool", start: 100, end: 104, tone: "confirm" });
  });

  it("builds caller and agent lanes from local audio signals", () => {
    const state = run([
      { type: "caller.speech", speaking: true, at: 1000 },
      { type: "caller.speech", speaking: false, at: 2400 },
      { type: "agent.audio", playing: true, at: 3500 },
      { type: "agent.audio", playing: false, at: 5000 },
    ]);
    expect(state.segments.map((s) => [s.lane, s.start, s.end])).toEqual([
      ["caller", 1000, 2400],
      ["agent", 3500, 5000],
    ]);
  });

  it("records latency and attaches perceived latency to the turn", () => {
    const state = run([
      server(
        {
          type: "metrics.turn",
          turn: 1,
          kind: "time_to_first_audio",
          latency_ms: 980,
          tool_ms: 0,
          tools: [],
        },
        3000,
      ),
      { type: "perceived", turn: 1, perceivedMs: 1040 },
    ]);
    expect(state.turns[0]).toMatchObject({ latencyMs: 980, perceivedMs: 1040, overlapped: false });
    expect(state.latency[0]).toMatchObject({ at: 3000, latencyMs: 980 });
  });

  it("closes open segments when the call ends", () => {
    const state = run([
      { type: "agent.audio", playing: true, at: 10 },
      server({ type: "call.ended", reason: "caller_hung_up", outcome: "booked" }, 50),
    ]);
    expect(state.status).toBe("ended");
    expect(state.outcome).toBe("booked");
    expect(state.segments[0].end).toBe(50);
  });

  it("treats fatal errors as a failed call", () => {
    const state = run([server({ type: "error", message: "No voice provider is configured.", fatal: true })]);
    expect(state.status).toBe("failed");
    expect(state.error).toMatch(/No voice provider/);
  });
});
