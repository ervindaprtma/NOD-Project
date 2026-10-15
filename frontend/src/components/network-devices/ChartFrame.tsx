"use client";

import { useEffect, useState, type ReactNode } from "react";

// Wraps a chart with a full-screen expand button. The child chart must fill its parent
// (className="h-full"); ChartFrame supplies the inline height and a big modal container,
// so the same chart renders small in place and large on expand. Esc / backdrop / ✕ close.
export function ChartFrame({ title, inlineClassName, children }: {
  title?: string;
  inlineClassName?: string;
  children: ReactNode;
}) {
  const [full, setFull] = useState(false);
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);

  return (
    <>
      <div className="relative">
        <button
          type="button"
          onClick={() => setFull(true)}
          title="Full screen"
          aria-label="Expand chart to full screen"
          className="absolute top-1 right-1 z-10 rounded-md border border-border bg-background/80 px-1.5 py-0.5 text-[12px] leading-none text-muted-foreground hover:bg-muted hover:text-foreground"
        >⤢</button>
        <div className={inlineClassName}>{children}</div>
      </div>
      {full ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setFull(false)}>
          <div className="w-full max-w-[1200px] rounded-lg border border-border bg-background p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-semibold truncate">{title || "Chart"}</span>
              <button type="button" onClick={() => setFull(false)} aria-label="Close"
                className="text-muted-foreground hover:text-foreground shrink-0 ml-2">✕</button>
            </div>
            <div className="h-[72vh]">{children}</div>
          </div>
        </div>
      ) : null}
    </>
  );
}
