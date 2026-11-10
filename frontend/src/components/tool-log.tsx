// Every tool call in order, with what it was asked and what it returned.
// Guardrail decisions are called out, since they are the interesting part.

import type { ToolActivity } from "@/lib/call-state";

import { clock, describeToolResult, formatMs } from "./format";

function tone(tool: ToolActivity): string {
  if (!tool.result) return "border-rule-strong";
  if (tool.guardrail === "confirmation_required") return "border-tool";
  return tool.ok ? "border-agent" : "border-interrupt";
}

export function ToolLog({ tools, emptyHint }: { tools: ToolActivity[]; emptyHint?: string }) {
  if (tools.length === 0) return <p className="text-sm text-ink-muted">{emptyHint}</p>;
  return (
    <ol className="space-y-2">
      {tools.map((tool) => (
        <li key={tool.id} className={`border-l-4 pl-3 ${tone(tool)}`}>
          <details className="group">
            <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-2 py-1 [&::-webkit-details-marker]:hidden">
              <span className="font-mono text-sm font-semibold">{tool.name}</span>
              <span className="text-sm">{describeToolResult(tool.name, tool.result, tool.guardrail)}</span>
              <span className="ml-auto text-xs tabular-nums text-ink-muted">
                {clock(tool.at)}
                {tool.durationMs !== undefined && ` in ${formatMs(tool.durationMs)}`}
              </span>
            </summary>
            <div className="grid gap-2 pb-2 text-xs sm:grid-cols-2">
              <div>
                <div className="mb-1 text-ink-muted">Arguments</div>
                <pre className="overflow-x-auto rounded bg-paper p-2 font-mono">
                  {JSON.stringify(tool.arguments, null, 2)}
                </pre>
              </div>
              <div>
                <div className="mb-1 text-ink-muted">Result</div>
                <pre className="max-h-64 overflow-auto rounded bg-paper p-2 font-mono">
                  {tool.result ? JSON.stringify(tool.result, null, 2) : "Waiting..."}
                </pre>
              </div>
            </div>
          </details>
        </li>
      ))}
    </ol>
  );
}
