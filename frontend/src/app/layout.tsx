import type { Metadata } from "next";
import { Atkinson_Hyperlegible_Mono, Atkinson_Hyperlegible_Next } from "next/font/google";
import Link from "next/link";

import "./globals.css";

// Atkinson Hyperlegible was drawn for low-vision readers; a front desk for a
// clinic should be easy to read for everyone.
const atkinson = Atkinson_Hyperlegible_Next({ variable: "--font-atkinson", subsets: ["latin"] });
const atkinsonMono = Atkinson_Hyperlegible_Mono({ variable: "--font-atkinson-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "CallPilot", template: "%s | CallPilot" },
  description:
    "A real-time AI voice receptionist. Call it from the browser and watch the transcript, tool calls and latency as it works.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${atkinson.variable} ${atkinsonMono.variable} antialiased`}>
      <body className="min-h-dvh">
        <header className="border-b border-rule bg-surface">
          <div className="mx-auto flex max-w-7xl flex-wrap items-baseline gap-x-6 gap-y-2 px-4 py-4 sm:px-6">
            <Link href="/" className="text-xl font-bold tracking-tight">
              CallPilot
            </Link>
            <p className="text-sm text-ink-muted">
              The front desk at Brightside Dental, a fictional clinic, answered by AI
            </p>
            <nav className="ml-auto flex gap-5 text-sm font-semibold">
              <Link href="/" className="hover:text-caller">
                Live call
              </Link>
              <Link href="/calls" className="hover:text-caller">
                Call log
              </Link>
            </nav>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
