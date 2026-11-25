"use client";

import { useEffect, type ReactNode } from "react";

// Modal shell — same markup as the TopologyCanvas Modal (copied, not shared, so the
// Topology component is left untouched). Backdrop click, ✕ and Esc close it.
export function PingModal({ title, maxWidth = "max-w-md", onClose, children }: {
  title: string;
  maxWidth?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className={`bg-background rounded-lg border border-border w-full ${maxWidth} p-4`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-sm">{title}</h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
