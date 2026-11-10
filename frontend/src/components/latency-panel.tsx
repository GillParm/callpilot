"use client";

// Per-turn latency: how long the caller waited for the agent to start talking.
// Bars are the server-side time to first audio (from the caller's last voiced
// frame); the tick is what the browser measured, network and playback included.

import { useState } from "react";

import type { TurnStats } from "@/lib/call-state";

import { axisLabel, formatMs, percentile } from "./format";

const CHART_HEIGHT = 128;

function niceCeiling(ms: number): { max: number; step: number } {
  const step = ms <= 1500 ? 500 : ms <= 4000 ? 1000 : 2000;
  return { max: Math.max(step * 2, Math.ceil(ms / step) * step), step };
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      <div className="text-xs text-ink-muted">{label}</div>
    </div>
  );
}

export function LatencyPanel({ turns, bargeIns }: { turns: TurnStats[]; bargeIns: number }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const measured = turns.filter((t) => !t.overlapped);
  const values = measured.map((t) => t.latencyMs);
  const p50 = percentile(values, 0.5);
  const p90 = percentile(values, 0.9);
  const isText = turns[0]?.kind === "time_to_first_token";
  const { max, step } = niceCeiling(
    Math.max(1, ...turns.map((t) => Math.max(t.latencyMs, t.perceivedMs ?? 0))),
  );
  const y = (ms: number) => (Math.min(ms, max) / max) * CHART_HEIGHT;
  const gridlines = Array.from({ length: Math.floor(max / step) + 1 }, (_, i) => i * step);
  const active = hovered === null ? null : turns.find((t) => t.turn === hovered);

  return (
    <div className="space-y-5">
      <p className="text-sm text-ink-muted">
        {isText
          ? "How long after each message Robin starts replying."
          : "How long the caller waits, from their last word to Robin's first sound."}
      </p>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat value={p50 === null ? "-" : formatMs(p50)} label="Median wait" />
        <Stat value={p90 === null ? "-" : formatMs(p90)} label="90th percentile" />
        <Stat value={String(measured.length)} label="Turns" />
        <Stat value={String(bargeIns)} label="Interruptions" />
      </div>

      {turns.length > 0 && (
        <figure>
          <div className="flex gap-2">
            <div
              className="relative w-9 shrink-0 text-right text-[11px] text-ink-muted"
              style={{ height: CHART_HEIGHT }}
            >
              {gridlines.map((g) => (
                <span
                  key={g}
                  className="absolute right-0 translate-y-1/2 tabular-nums"
                  style={{ bottom: y(g) }}
                >
                  {axisLabel(g)}
                </span>
              ))}
            </div>
            <div
              className="relative flex-1"
              style={{ height: CHART_HEIGHT }}
              onMouseLeave={() => setHovered(null)}
            >
              {gridlines.map((g) => (
                <div
                  key={g}
                  className={`absolute inset-x-0 h-px ${g === 0 ? "bg-rule-strong" : "bg-rule"}`}
                  style={{ bottom: y(g) }}
                  aria-hidden
                />
              ))}
              <div className="absolute inset-0 flex items-end gap-0.5">
                {turns.map((turn) => (
                  <div
                    key={turn.turn}
                    className="relative flex h-full max-w-6 flex-1 items-end justify-center"
                    onMouseEnter={() => setHovered(turn.turn)}
                    onFocus={() => setHovered(turn.turn)}
                    tabIndex={0}
                    aria-label={`Turn ${turn.turn}: ${formatMs(turn.latencyMs)}`}
                  >
                    <div
                      className={`w-full rounded-t ${turn.overlapped ? "bg-rule-strong" : "bg-agent"}`}
                      style={{ height: Math.max(2, y(turn.latencyMs)) }}
                    />
                    {turn.perceivedMs !== undefined && (
                      <div
                        className="absolute inset-x-[-3px] h-0.5 bg-ink"
                        style={{ bottom: y(turn.perceivedMs) }}
                        aria-hidden
                      />
                    )}
                  </div>
                ))}
              </div>
              {active && (
                <div className="pointer-events-none absolute -top-2 right-0 z-10 max-w-64 rounded bg-ink px-2 py-1.5 text-xs text-surface">
                  <div className="font-semibold">Turn {active.turn}</div>
                  <div>
                    {formatMs(active.latencyMs)} to first {isText ? "token" : "audio"}
                    {active.overlapped && " (caller was still talking)"}
                  </div>
                  {active.endpointingMs !== undefined && (
                    <div>{formatMs(active.endpointingMs)} of it detecting end of speech</div>
                  )}
                  {active.perceivedMs !== undefined && (
                    <div>{formatMs(active.perceivedMs)} heard in the browser</div>
                  )}
                  {active.tools.length > 0 && <div>Tools: {active.tools.join(", ")}</div>}
                </div>
              )}
            </div>
          </div>
          <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 pl-11 text-xs text-ink-muted">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-3 w-2 rounded-t-sm bg-agent" /> Server, from the caller&apos;s
              last word
            </span>
            {!isText && (
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-0.5 w-3 bg-ink" /> Heard in the browser
              </span>
            )}
            {turns.some((t) => t.overlapped) && (
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-3 w-2 rounded-t-sm bg-rule-strong" /> Caller still talking,
                not counted
              </span>
            )}
          </figcaption>
          <table className="sr-only">
            <caption>Latency per turn</caption>
            <thead>
              <tr>
                <th>Turn</th>
                <th>Server</th>
                <th>Browser</th>
                <th>Tools</th>
              </tr>
            </thead>
            <tbody>
              {turns.map((t) => (
                <tr key={t.turn}>
                  <td>{t.turn}</td>
                  <td>{formatMs(t.latencyMs)}</td>
                  <td>{t.perceivedMs === undefined ? "" : formatMs(t.perceivedMs)}</td>
                  <td>{t.tools.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </figure>
      )}
    </div>
  );
}
