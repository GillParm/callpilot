"use client";

// The conversation laid out like a transcript: speaker in the margin, words
// beside it, so long calls stay easy to scan.

import { useEffect, useRef, useState } from "react";

import type { TranscriptLine } from "@/lib/call-state";

import { clock } from "./format";

const SPEAKER = { caller: "Caller", agent: "Robin" } as const;
const MARK = { caller: "bg-caller", agent: "bg-agent" } as const;

export function Transcript({
  lines,
  canType,
  onSend,
  emptyHint,
}: {
  lines: TranscriptLine[];
  canType?: boolean;
  onSend?: (text: string) => void;
  emptyHint?: string;
}) {
  const scroller = useRef<HTMLOListElement>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [lines]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ol
        ref={scroller}
        className="min-h-48 flex-1 space-y-3 overflow-y-auto pr-1"
        aria-live="polite"
        aria-relevant="additions"
      >
        {lines.length === 0 && <li className="text-sm text-ink-muted">{emptyHint}</li>}
        {lines.map((line) => (
          <li key={line.id} className="grid grid-cols-[4.5rem_1fr] gap-x-3">
            <div className="pt-0.5 text-sm">
              <div className="flex items-center gap-1.5 font-semibold">
                <span className={`inline-block h-2.5 w-1 rounded-full ${MARK[line.role]}`} aria-hidden />
                {SPEAKER[line.role]}
              </div>
              <div className="pl-2.5 text-xs tabular-nums text-ink-muted">{clock(line.at)}</div>
            </div>
            <p className="max-w-prose leading-relaxed">
              {line.text}
              {line.streaming && (
                <span className="ml-0.5 inline-block h-4 w-px animate-pulse bg-ink align-middle" />
              )}
              {line.interrupted && (
                <span className="ml-1.5 text-sm text-ink-muted">
                  <span className="text-interrupt" aria-hidden>
                    &#8213;
                  </span>{" "}
                  cut off by the caller
                </span>
              )}
            </p>
          </li>
        ))}
      </ol>

      {onSend && (
        <form
          className="mt-4 flex gap-2 border-t border-rule pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            onSend(draft);
            setDraft("");
          }}
        >
          <label htmlFor="caller-message" className="sr-only">
            Message to Robin
          </label>
          <input
            id="caller-message"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={!canType}
            placeholder={canType ? "Type to Robin" : "Start a call to type to Robin"}
            className="min-w-0 flex-1 rounded-md border border-rule-strong bg-surface px-3 py-2 placeholder:text-ink-muted disabled:bg-paper"
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={!canType || !draft.trim()}
            className="rounded-md bg-ink px-4 py-2 font-semibold text-surface disabled:opacity-40"
          >
            Send
          </button>
        </form>
      )}
    </div>
  );
}
