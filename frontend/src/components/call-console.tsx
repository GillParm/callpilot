"use client";

import { Keyboard, Mic, Phone, PhoneOff } from "lucide-react";
import { useEffect, useState } from "react";

import type { BackendConfig, Mode } from "@/lib/protocol";
import { useCall } from "@/lib/use-call";

import { CallScore } from "./call-score";
import { clock, describeOutcome } from "./format";
import { LatencyPanel } from "./latency-panel";
import { LevelMeter } from "./level-meter";
import { ToolLog } from "./tool-log";
import { Transcript } from "./transcript";

function useElapsed(running: boolean, startedAt: number | null): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!running || startedAt === null) return;
    const timer = setInterval(() => setNow(performance.now() - startedAt), 100);
    return () => clearInterval(timer);
  }, [running, startedAt]);
  return now;
}

function Panel({
  title,
  children,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`border-t border-rule pt-4 ${className}`}>
      <h2 className="mb-4 text-lg font-bold">{title}</h2>
      {children}
    </section>
  );
}

export function CallConsole({ backendUrl, config }: { backendUrl: string; config: BackendConfig | null }) {
  const { state, start, hangUp, sendText, levelRef, startedAt } = useCall(backendUrl);
  const voiceReady = Boolean(config?.providers.voice);
  const textReady = Boolean(config?.providers.text);
  const [mode, setMode] = useState<Mode>(voiceReady || !textReady ? "voice" : "text");
  const live = state.status === "live" || state.status === "connecting";
  const elapsed = useElapsed(live, startedAt);
  const lastSegmentEnd = Math.max(0, ...state.segments.map((s) => s.end ?? 0));
  const nowMs = live ? elapsed : lastSegmentEnd;
  const canStart = mode === "voice" ? voiceReady : textReady;

  let status = "Not on a call";
  if (state.status === "connecting") status = "Connecting...";
  if (state.status === "live") status = state.agentSpeaking ? "Robin is speaking" : "Robin is listening";
  if (state.status === "ended") status = `Call ended. ${describeOutcome(state.outcome)}.`;
  if (state.status === "failed") status = state.error ?? "The call failed.";

  return (
    <div className="mx-auto max-w-7xl px-4 pb-16 sm:px-6">
      <section className="grid gap-8 py-10 lg:grid-cols-[1fr_auto] lg:items-end">
        <div className="max-w-2xl">
          <h1 className="text-4xl leading-[1.05] font-bold tracking-tight sm:text-6xl">
            Call the front desk.
          </h1>
          <p className="mt-4 text-lg text-ink-muted">
            Robin is an AI receptionist that books, moves and cancels appointments. Talk to it, interrupt it,
            ask it something off-topic. Everything it hears, decides and does appears below as it happens.
          </p>
        </div>

        <div className="flex flex-col gap-3 lg:items-end">
          <div
            role="radiogroup"
            aria-label="How to talk to Robin"
            className="flex rounded-md border border-rule-strong bg-surface p-0.5 text-sm font-semibold"
          >
            {(
              [
                { value: "voice", label: "Talk", icon: Mic, ready: voiceReady },
                { value: "text", label: "Type", icon: Keyboard, ready: textReady },
              ] as const
            ).map(({ value, label, icon: Icon, ready }) => (
              <button
                key={value}
                role="radio"
                aria-checked={mode === value}
                disabled={live || !ready}
                onClick={() => setMode(value)}
                className={`flex items-center gap-1.5 rounded px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40 ${
                  mode === value ? "bg-ink text-surface" : ""
                }`}
              >
                <Icon className="size-4" aria-hidden /> {label}
              </button>
            ))}
          </div>

          {live ? (
            <button
              onClick={hangUp}
              className="flex items-center justify-center gap-2 rounded-full bg-interrupt px-8 py-4 text-lg font-bold text-surface shadow-[0_0_0_6px_var(--color-interrupt-wash)]"
            >
              <PhoneOff className="size-5" aria-hidden /> Hang up
            </button>
          ) : (
            <button
              onClick={() => start(mode)}
              disabled={!canStart}
              className="flex items-center justify-center gap-2 rounded-full bg-agent px-8 py-4 text-lg font-bold text-surface disabled:opacity-40"
            >
              <Phone className="size-5" aria-hidden />{" "}
              {mode === "voice" ? "Call the front desk" : "Start a text chat"}
            </button>
          )}

          <div className="flex items-center gap-3 text-sm" aria-live="polite">
            {mode === "voice" && <LevelMeter levelRef={levelRef} active={state.status === "live"} />}
            <span className={state.status === "failed" ? "text-interrupt" : ""}>{status}</span>
            {live && <span className="tabular-nums text-ink-muted">{clock(elapsed)}</span>}
          </div>
          {state.model && (
            <p className="text-xs text-ink-muted">
              {state.provider} / {state.model}
            </p>
          )}
        </div>
      </section>

      {!config && (
        <p className="mb-6 rounded-md border border-interrupt bg-interrupt-wash px-4 py-3 text-sm">
          The call server isn&apos;t reachable at <code className="font-mono">{backendUrl}</code>. Start it
          with <code className="font-mono">make dev-backend</code> or{" "}
          <code className="font-mono">docker compose up</code>.
        </p>
      )}
      {config && !voiceReady && !textReady && (
        <p className="mb-6 rounded-md border border-tool bg-tool-wash px-4 py-3 text-sm">
          No model provider is configured. Add <code className="font-mono">GEMINI_API_KEY</code> (free) or{" "}
          <code className="font-mono">OPENAI_API_KEY</code> for voice, or{" "}
          <code className="font-mono">ANTHROPIC_API_KEY</code> for text, to{" "}
          <code className="font-mono">.env</code> and restart the backend.
        </p>
      )}

      <section
        className="rounded-lg border border-rule bg-surface px-4 py-5 sm:px-6"
        aria-label="Call timeline"
      >
        <CallScore
          segments={state.segments}
          latency={state.latency}
          bargeIns={state.bargeIns}
          nowMs={nowMs}
        />
      </section>

      <div className="mt-8 grid gap-x-10 gap-y-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Panel title="Transcript" className="flex max-h-[34rem] flex-col">
          <Transcript
            lines={state.transcript}
            canType={state.status === "live"}
            onSend={sendText}
            emptyHint="Start a call and say hello. Try asking for a cleaning next week."
          />
        </Panel>

        <div className="space-y-8">
          <Panel title="Response time">
            <LatencyPanel turns={state.turns} bargeIns={state.bargeIns.length} />
          </Panel>
          <Panel title="Tool calls">
            <ToolLog
              tools={state.tools}
              emptyHint="Tool calls show up here, with their arguments and results."
            />
          </Panel>
          {config && config.demo_patients.length > 0 && (
            <Panel title="Try it as a returning patient">
              <p className="mb-3 text-sm text-ink-muted">
                Made-up patients on file. Maria Lopez already has a cleaning booked, so you can try moving or
                cancelling it.
              </p>
              <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                {config.demo_patients.map((p) => (
                  <li key={p.name} className="flex justify-between gap-3">
                    <span className="font-semibold">{p.name}</span>
                    <span className="tabular-nums text-ink-muted">
                      {new Date(`${p.date_of_birth}T12:00:00`).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
