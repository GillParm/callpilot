import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { clock, describeOutcome, formatMs } from "@/components/format";
import { LocalTime } from "@/components/local-time";
import { backendFetch } from "@/lib/backend";
import type { CallRecord } from "@/lib/recorded-call";

export const metadata: Metadata = { title: "Call log" };

const HOW = { voice: "Voice", text: "Text" } as const;

async function CallList() {
  await connection();
  const calls = await backendFetch<CallRecord[]>("/api/calls?limit=100");

  if (calls === null) {
    return (
      <p className="rounded-md border border-interrupt bg-interrupt-wash px-4 py-3 text-sm">
        The call server isn&apos;t reachable, so the log can&apos;t load. Start the backend and refresh.
      </p>
    );
  }
  if (calls.length === 0) {
    return (
      <p className="text-ink-muted">
        No calls yet.{" "}
        <Link href="/" className="font-semibold text-caller underline underline-offset-4">
          Make one from the live console
        </Link>{" "}
        and it will show up here with its summary.
      </p>
    );
  }

  return (
    <ol className="divide-y divide-rule border-y border-rule">
      <li className="hidden grid-cols-[8rem_9rem_11rem_4rem_6rem_1fr] gap-4 py-2 text-xs text-ink-muted md:grid">
        <span>When</span>
        <span>Call</span>
        <span>Outcome</span>
        <span>Length</span>
        <span>Median wait</span>
        <span>Summary</span>
      </li>
      {calls.map((call) => (
        <li key={call.id}>
          <Link
            href={`/calls/${call.id}`}
            className="grid gap-x-4 gap-y-1 py-4 hover:bg-surface md:grid-cols-[8rem_9rem_11rem_4rem_6rem_1fr] md:items-baseline"
          >
            <span className="text-sm tabular-nums">
              <LocalTime iso={call.started_at} />
            </span>
            <span className="text-sm">
              {HOW[call.mode]} {call.channel === "phone" ? "phone call" : "in browser"}
            </span>
            <span className="font-semibold">{describeOutcome(call.outcome) || "In progress"}</span>
            <span className="text-sm tabular-nums">
              {call.duration_s === null ? "" : clock(call.duration_s * 1000)}
            </span>
            <span className="text-sm tabular-nums">
              {call.stats.latency_p50_ms === null ? "" : formatMs(call.stats.latency_p50_ms)}
            </span>
            <span className="line-clamp-2 text-sm text-ink-muted">
              {call.summary ?? "Summary on its way."}
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}

export default function CallsPage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
      <h1 className="text-4xl font-bold tracking-tight">Call log</h1>
      <p className="mt-2 mb-8 max-w-2xl text-ink-muted">
        Every call Robin has taken, newest first, with an after-call summary for the clinic team. Open one to
        replay its timeline, transcript and tool calls.
      </p>
      <Suspense fallback={<p className="text-ink-muted">Loading calls...</p>}>
        <CallList />
      </Suspense>
    </main>
  );
}
