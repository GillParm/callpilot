"use client";

// The call score: who was making sound, and when, on three lanes over the
// call's timeline. The gap between the caller finishing and the agent's first
// sound is labelled with its latency; barge-ins are drawn across the lanes.

import { useState } from "react";

import type { LatencyMark, Lane, Segment } from "@/lib/call-state";

import { clock, formatMs } from "./format";

const LANES: { lane: Lane; label: string }[] = [
  { lane: "caller", label: "Caller" },
  { lane: "agent", label: "Robin" },
  { lane: "tool", label: "Tools" },
];

const TICK_STEPS_S = [1, 2, 5, 10, 15, 30, 60, 120];

function tickStep(spanMs: number): number {
  const seconds = spanMs / 1000;
  return (TICK_STEPS_S.find((step) => seconds / step <= 8) ?? 300) * 1000;
}

const segmentClass = (segment: Segment) => {
  if (segment.lane === "caller") return "h-3 rounded bg-caller";
  if (segment.lane === "agent") return "h-3 rounded bg-agent";
  if (segment.tone === "confirm") return "h-4 rounded-sm border-2 border-tool bg-tool-wash";
  if (segment.tone === "blocked") return "h-4 rounded-sm border-2 border-interrupt bg-interrupt-wash";
  return "h-4 rounded-sm bg-tool";
};

interface Hover {
  x: number;
  text: string;
}

export interface CallScoreProps {
  segments: Segment[];
  latency: LatencyMark[];
  bargeIns: number[];
  /** Current time for a live call, or the call's duration for a recorded one. */
  nowMs: number;
}

export function CallScore({ segments, latency, bargeIns, nowMs }: CallScoreProps) {
  const [hover, setHover] = useState<Hover | null>(null);
  const step = tickStep(Math.max(nowMs, 15_000));
  const span = Math.max(15_000, Math.ceil((nowMs + step / 4) / step) * step);
  const x = (ms: number) => `${Math.min(100, (ms / span) * 100)}%`;
  const ticks = Array.from({ length: Math.floor(span / step) + 1 }, (_, i) => i * step);

  const describe = (segment: Segment) => {
    const end = segment.end ?? nowMs;
    const who =
      segment.lane === "tool" ? (segment.label ?? "tool") : LANES.find((l) => l.lane === segment.lane)?.label;
    return `${who}, ${clock(segment.start)} for ${formatMs(end - segment.start)}`;
  };

  return (
    <figure className="relative" aria-label="Timeline of the call: caller, Robin and tool activity">
      <div className="grid grid-cols-[4rem_1fr] gap-x-3">
        <div aria-hidden />
        <div className="relative h-6 text-xs text-ink-muted" aria-hidden>
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2 tabular-nums" style={{ left: x(t) }}>
              {clock(t)}
            </span>
          ))}
        </div>

        <div className="flex flex-col text-sm font-semibold">
          {LANES.map(({ lane, label }) => (
            <div key={lane} className="flex h-12 items-center">
              {label}
            </div>
          ))}
        </div>

        <div className="relative" onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <div key={t} className="absolute inset-y-0 w-px bg-rule/60" style={{ left: x(t) }} aria-hidden />
          ))}

          {LANES.map(({ lane }) => (
            <div key={lane} className="relative flex h-12 items-center">
              {/* The staff line the lane's marks sit on. */}
              <div className="absolute inset-x-0 top-1/2 h-px bg-rule-strong" aria-hidden />
              {segments
                .filter((s) => s.lane === lane)
                .map((segment) => {
                  const end = segment.end ?? nowMs;
                  return (
                    <div
                      key={segment.id}
                      role="img"
                      aria-label={describe(segment)}
                      className={`absolute min-w-1.5 ${segmentClass(segment)}`}
                      style={{ left: x(segment.start), width: x(end - segment.start) }}
                      onMouseEnter={(event) => {
                        const box = event.currentTarget.parentElement!.getBoundingClientRect();
                        setHover({ x: event.clientX - box.left, text: describe(segment) });
                      }}
                    />
                  );
                })}
              {lane === "agent" &&
                latency
                  .filter((mark) => !mark.overlapped && mark.latencyMs > 0)
                  .map((mark) => (
                    <div
                      key={`lat-${mark.turn}`}
                      className="absolute top-0.5 flex h-4 items-start justify-center border-x border-t border-ink-muted"
                      style={{ left: x(mark.at - mark.latencyMs), width: x(mark.latencyMs) }}
                    >
                      <span className="-mt-3.5 bg-surface px-1 text-[11px] leading-none whitespace-nowrap tabular-nums text-ink-muted">
                        {formatMs(mark.latencyMs)}
                      </span>
                    </div>
                  ))}
            </div>
          ))}

          {bargeIns.map((at) => (
            <div
              key={`barge-${at}`}
              className="absolute top-0 h-24 w-0.5 bg-interrupt"
              style={{ left: x(at) }}
              role="img"
              aria-label={`Caller interrupted at ${clock(at)}`}
            />
          ))}

          {segments.length === 0 && (
            <p className="absolute inset-0 flex items-center justify-center text-sm text-ink-muted">
              <span className="bg-surface px-3">The timeline fills in as the call happens.</span>
            </p>
          )}

          {hover && (
            <div
              className="pointer-events-none absolute -top-8 z-10 -translate-x-1/2 rounded bg-ink px-2 py-1 text-xs whitespace-nowrap text-surface"
              style={{ left: hover.x }}
            >
              {hover.text}
            </div>
          )}
        </div>
      </div>

      <figcaption className="mt-3 flex flex-wrap gap-x-5 gap-y-1 pl-[4.75rem] text-xs text-ink-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-4 rounded-sm bg-tool" /> Tool ran
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-4 rounded-sm border-2 border-tool bg-tool-wash" /> Read back,
          waiting for a yes
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-4 rounded-sm border-2 border-interrupt bg-interrupt-wash" />{" "}
          Blocked by a guardrail
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-0.5 bg-interrupt" /> Caller interrupted
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-4 border-x border-t border-ink-muted" /> Time to first audio
        </span>
      </figcaption>
    </figure>
  );
}
