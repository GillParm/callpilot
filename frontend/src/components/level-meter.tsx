"use client";

// Mic level, driven straight from a ref on animation frames so 50 audio
// frames a second don't re-render React.

import { useEffect, useRef, type RefObject } from "react";

const BARS = 10;

export function LevelMeter({ levelRef, active }: { levelRef: RefObject<number>; active: boolean }) {
  const bars = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    if (!active) return;
    const elements = bars.current;
    let frame = 0;
    const draw = () => {
      const lit = Math.round(levelRef.current * BARS);
      elements.forEach((bar, i) => bar?.classList.toggle("bg-caller", i < lit));
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      elements.forEach((bar) => bar?.classList.remove("bg-caller"));
    };
  }, [active, levelRef]);

  return (
    <div className="flex h-4 items-end gap-0.5" aria-hidden>
      {Array.from({ length: BARS }, (_, i) => (
        <span
          key={i}
          ref={(el) => {
            bars.current[i] = el;
          }}
          className="w-1 rounded-sm bg-rule-strong"
          style={{ height: `${30 + i * 7}%` }}
        />
      ))}
    </div>
  );
}
