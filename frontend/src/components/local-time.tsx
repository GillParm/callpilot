"use client";

// Timestamps in the viewer's own time zone. The server can't know it, so the
// server-rendered text is replaced on the client.

export function LocalTime({ iso, withDate = true }: { iso: string; withDate?: boolean }) {
  const date = new Date(iso);
  const text = date.toLocaleString(undefined, {
    ...(withDate ? { month: "short", day: "numeric" } : {}),
    hour: "numeric",
    minute: "2-digit",
  });
  return (
    <time dateTime={iso} suppressHydrationWarning>
      {text}
    </time>
  );
}
