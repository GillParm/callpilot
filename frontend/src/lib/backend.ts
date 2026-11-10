import "server-only";

import type { BackendConfig } from "./protocol";

/** Where the browser reaches the backend (HTTP and WebSocket). Read at request time. */
export function publicBackendUrl(): string {
  return process.env.BACKEND_PUBLIC_URL ?? "http://localhost:8000";
}

/** Where the Next.js server reaches the backend (a different host inside docker compose). */
export function internalBackendUrl(): string {
  return process.env.BACKEND_INTERNAL_URL ?? publicBackendUrl();
}

export async function backendFetch<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(new URL(path, internalBackendUrl()), { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export const fetchBackendConfig = () => backendFetch<BackendConfig>("/api/config");
