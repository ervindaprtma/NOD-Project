"use client";

import type { PingTarget } from "./types";
import { fmtMs } from "./types";

// KPI strip (page design §2.3) — same tile markup as network-devices OverviewCards.Kpi.
// Expected-down and disabled targets are excluded from every incident count.
export function PingKpis({ targets }: { targets: PingTarget[] }) {
  const monitored = targets.filter((t) => t.enabled && !t.expected_down);
  const reachable = monitored.filter((t) => t.status === "up" || t.status === "degraded");
  const degraded = monitored.filter((t) => t.status === "degraded").length;
  const down = monitored.filter((t) => t.status === "down").length;
  const expected = targets.filter((t) => t.enabled && t.expected_down).length;
  const lats = reachable.map((t) => t.metrics.avg_ms).filter((v): v is number => v != null);
  const avg = lats.length ? lats.reduce((a, b) => a + b, 0) / lats.length : null;
  const lossy = monitored.filter((t) => (t.metrics.loss_pct ?? 0) > 0).length;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
      <Kpi label="Reachable" value={`${reachable.length} / ${monitored.length}`} />
      <Kpi label="Degraded" value={String(degraded)} />
      <Kpi label="Down" value={String(down)} hint={expected ? `+ ${expected} expected down` : undefined} />
      <Kpi label="Avg latency" value={fmtMs(avg)} />
      <Kpi label="With packet loss" value={String(lossy)} />
    </div>
  );
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums mt-1">{value}</div>
      {hint ? <div className="text-[11px] text-muted-foreground mt-0.5">{hint}</div> : null}
    </div>
  );
}
