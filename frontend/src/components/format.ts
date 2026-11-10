// Formatting shared by the console and the call log.

export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

/** Compact labels for chart axes: "500 ms", "1.5 s", "2 s". */
export function axisLabel(ms: number): string {
  if (ms === 0) return "0";
  if (ms < 1000) return `${ms} ms`;
  return `${Number((ms / 1000).toFixed(1))} s`;
}

export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function percentile(values: number[], pct: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const k = (sorted.length - 1) * pct;
  const lo = Math.floor(k);
  const hi = Math.min(lo + 1, sorted.length - 1);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (k - lo);
}

const OUTCOMES: Record<string, string> = {
  booked: "Appointment booked",
  cancelled: "Appointment cancelled",
  rescheduled: "Appointment moved",
  escalated: "Handed to the clinic team",
  info: "Questions answered",
  abandoned: "Caller hung up",
};

export const describeOutcome = (outcome: string | null) => (outcome ? (OUTCOMES[outcome] ?? outcome) : "");

/** One short phrase for what a tool call did, from its result. */
export function describeToolResult(
  name: string,
  result: Record<string, unknown> | undefined,
  guardrail: string | null | undefined,
): string {
  if (!result) return "Running";
  if (guardrail === "confirmation_required") return "Read back, waiting for a yes";
  if (result.ok === false) {
    const error = result.error as { message?: string } | undefined;
    return guardrail ? `Blocked: ${error?.message ?? guardrail}` : `Failed: ${error?.message ?? "error"}`;
  }
  switch (name) {
    case "check_availability": {
      const slots = (result.slots as unknown[] | undefined) ?? [];
      return slots.length === 1 ? "1 opening" : `${slots.length} openings`;
    }
    case "lookup_patient":
      return result.found ? "Patient verified" : "No matching patient";
    case "book_appointment":
      return `Booked for ${String(result.when ?? "")}`.trim();
    case "cancel_appointment":
      return result.late_cancellation ? "Cancelled (less than 24 hours' notice)" : "Cancelled";
    case "escalate_to_human":
      return `Ticket ${String(result.ticket_id ?? "")} for the clinic team`.trim();
    default:
      return "Done";
  }
}
