import { connection } from "next/server";
import { Suspense } from "react";

import { CallConsole } from "@/components/call-console";
import { fetchBackendConfig, publicBackendUrl } from "@/lib/backend";

async function Console() {
  // Backend URLs come from the environment at request time, not build time.
  await connection();
  const config = await fetchBackendConfig();
  return <CallConsole backendUrl={publicBackendUrl()} config={config} />;
}

export default function Home() {
  return (
    <Suspense
      fallback={<div className="mx-auto max-w-7xl px-6 py-10 text-ink-muted">Loading the console...</div>}
    >
      <Console />
    </Suspense>
  );
}
