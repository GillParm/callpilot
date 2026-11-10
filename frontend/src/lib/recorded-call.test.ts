import { describe, expect, it } from "vitest";

import { replay, type CallDetail } from "./recorded-call";

const call: CallDetail = {
  id: "call_1",
  channel: "browser",
  mode: "voice",
  provider: "gemini",
  model: "gemini-3.8-live",
  started_at: "2026-11-10T21:00:00Z",
  ended_at: "2026-11-10T21:01:00Z",
  duration_s: 60,
  outcome: "booked",
  summary: "Booked.",
  caller_number: null,
  stats: {
    turns: 1,
    latency_p50_ms: 900,
    latency_p90_ms: 900,
    barge_ins: 1,
    tool_calls: 1,
    guardrail_blocks: 0,
  },
  events: [
    { at: "", elapsed_ms: 100, type: "transcript", payload: { role: "agent", text: "Hello" } },
    { at: "", elapsed_ms: 3000, type: "segment", payload: { lane: "caller", start_ms: 1200, end_ms: 3000 } },
    {
      at: "",
      elapsed_ms: 3400,
      type: "tool_call",
      payload: {
        id: "t1",
        name: "book_appointment",
        arguments: {},
        result: { ok: true, status: "confirmation_required" },
        ok: true,
        guardrail: "confirmation_required",
        duration_ms: 2.4,
      },
    },
    {
      at: "",
      elapsed_ms: 3900,
      type: "latency",
      payload: {
        turn: 1,
        kind: "time_to_first_audio",
        latency_ms: 900,
        tool_ms: 2.4,
        tools: ["book_appointment"],
      },
    },
    { at: "", elapsed_ms: 4100, type: "client_latency", payload: { turn: 1, perceived_ms: 960 } },
    { at: "", elapsed_ms: 5000, type: "barge_in", payload: { trigger: "caller_speech" } },
    { at: "", elapsed_ms: 6000, type: "segment", payload: { lane: "agent", start_ms: 3900, end_ms: 5000 } },
  ],
};

describe("replay", () => {
  const replayed = replay(call);

  it("rebuilds the transcript and timeline lanes", () => {
    expect(replayed.transcript[0]).toMatchObject({ role: "agent", text: "Hello", at: 100 });
    expect(replayed.segments.map((s) => [s.lane, s.start, s.end])).toEqual([
      ["caller", 1200, 3000],
      ["tool", 3398, 3400],
      ["agent", 3900, 5000],
    ]);
    expect(replayed.segments[1].tone).toBe("confirm");
  });

  it("joins server and browser latency for each turn", () => {
    expect(replayed.turns[0]).toMatchObject({
      latencyMs: 900,
      perceivedMs: 960,
      tools: ["book_appointment"],
    });
    expect(replayed.bargeIns).toEqual([5000]);
    expect(replayed.durationMs).toBe(6000);
  });
});
