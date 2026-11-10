import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { CallScore } from "@/components/call-score";
import { clock, describeOutcome } from "@/components/format";
import { LatencyPanel } from "@/components/latency-panel";
import { LocalTime } from "@/components/local-time";
import { ToolLog } from "@/components/tool-log";
import { Transcript } from "@/components/transcript";
import { backendFetch } from "@/lib/backend";
import { replay, type CallDetail } from "@/lib/recorded-call";

export const metadata: Metadata = { title: "Call details" };

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className="font-semibold">{children}</dd>
    </div>
  );
}

async function CallView({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const call = await backendFetch<CallDetail>(`/api/calls/${encodeURIComponent(id)}`);
  if (call === null) notFound();
  const replayed = replay(call);

  return (
    <>
      <h1 className="text-4xl font-bold tracking-tight">
        {describeOutcome(call.outcome) || "Call in progress"}
      </h1>
      <dl className="mt-4 flex flex-wrap gap-x-10 gap-y-3">
        <Fact label="Started">
          <LocalTime iso={call.started_at} />
        </Fact>
        <Fact label="Length">{call.duration_s === null ? "Still live" : clock(call.duration_s * 1000)}</Fact>
        <Fact label="Mode">{call.mode === "voice" ? "Voice" : "Text"}</Fact>
        <Fact label="Model">
          <span className="font-mono text-sm">
            {call.provider}/{call.model}
          </span>
        </Fact>
        {call.stats.guardrail_blocks > 0 && (
          <Fact label="Guardrail blocks">{call.stats.guardrail_blocks}</Fact>
        )}
      </dl>

      <section className="mt-8 max-w-3xl">
        <h2 className="mb-2 text-lg font-bold">Summary for the clinic team</h2>
        <p className="text-lg leading-relaxed">
          {call.summary ?? "The summary is still being written. Refresh in a moment."}
        </p>
      </section>

      <section
        className="mt-8 rounded-lg border border-rule bg-surface px-4 py-5 sm:px-6"
        aria-label="Call timeline"
      >
        <CallScore
          segments={replayed.segments}
          latency={replayed.latency}
          bargeIns={replayed.bargeIns}
          nowMs={replayed.durationMs}
        />
      </section>

      <div className="mt-8 grid gap-x-10 gap-y-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <section className="border-t border-rule pt-4">
          <h2 className="mb-4 text-lg font-bold">Transcript</h2>
          <Transcript lines={replayed.transcript} emptyHint="Nothing was said on this call." />
        </section>
        <div className="space-y-8">
          <section className="border-t border-rule pt-4">
            <h2 className="mb-4 text-lg font-bold">Response time</h2>
            <LatencyPanel turns={replayed.turns} bargeIns={replayed.bargeIns.length} />
          </section>
          <section className="border-t border-rule pt-4">
            <h2 className="mb-4 text-lg font-bold">Tool calls</h2>
            <ToolLog tools={replayed.tools} emptyHint="Robin didn't need any tools on this call." />
          </section>
        </div>
      </div>
    </>
  );
}

export default function CallPage({ params }: PageProps<"/calls/[id]">) {
  return (
    <main className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
      <Link href="/calls" className="text-sm font-semibold text-caller underline-offset-4 hover:underline">
        All calls
      </Link>
      <div className="mt-4">
        <Suspense fallback={<p className="text-ink-muted">Loading the call...</p>}>
          <CallView params={params} />
        </Suspense>
      </div>
    </main>
  );
}
