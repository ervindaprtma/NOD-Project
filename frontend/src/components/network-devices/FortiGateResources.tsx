"use client";

import useSWR from "swr";
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import { swrFetcherLong } from "@/lib/api";
import type { Envelope } from "./types";
import { FGT_SITE_NAME } from "./types";
import type { ResourceData, DeviceResourceStatus } from "@/types";

// FortiGate + HA resource summary on the Overview tab: CPU% / RAM% donut gauges +
// session count, per site (DC/DRC/Office). DC is an HA cluster, so its two members
// each render. Reuses the existing /api/v1/resources endpoint — no backend change.
const SITES: { key: string; label: string }[] = [
  { key: "DC", label: "DC" },
  { key: "DRC", label: "DRC" },
  { key: "Office", label: "Office" },
];

export function FortiGateResources() {
  return (
    <div className="mt-6">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
        FortiGate Resources (CPU · RAM · Sessions)
      </h3>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {SITES.map((s) => <SiteResources key={s.key} siteLabel={s.label} />)}
      </div>
    </div>
  );
}

function SiteResources({ siteLabel }: { siteLabel: string }) {
  const siteName = FGT_SITE_NAME[siteLabel];
  // /resources requires gte_ms/lte_ms (no window param); last 15 min for a current read.
  // Round to the minute: a raw Date.now() recomputes every render, changing the SWR
  // key each time → endless revalidation → stuck on "Loading…".
  const now = Math.floor(Date.now() / 60_000) * 60_000;
  const gte = now - 15 * 60 * 1000;
  const { data, error, isLoading } = useSWR<Envelope<ResourceData>>(
    siteName ? `/api/v1/resources?site_name=${siteName}&gte_ms=${gte}&lte_ms=${now}` : null,
    swrFetcherLong,
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );
  const devices = data?.data?.current ?? [];

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-sm font-semibold mb-2">{siteLabel}</div>
      {isLoading ? (
        <p className="text-xs text-muted-foreground py-4">Loading…</p>
      ) : error ? (
        <p className="text-xs text-red-500 py-4">Failed to load.</p>
      ) : devices.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4">No resource data.</p>
      ) : (
        <div className="space-y-3">
          {/* HA members share device="FGT-DC"; key on the unique serial so both render */}
          {devices.map((d) => <DeviceResource key={d.serial_number || d.hostname || d.device} d={d} isHA={devices.length > 1} />)}
        </div>
      )}
    </div>
  );
}

function DeviceResource({ d, isHA }: { d: DeviceResourceStatus; isHA: boolean }) {
  const inSync = d.sync_status === "1" || (d.sync_status || "").toLowerCase().includes("sync");
  return (
    <div>
      {isHA ? (
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="font-medium">{d.hostname || d.device}</span>
          <span className="text-muted-foreground">HA {inSync ? "● in-sync" : "○ out-of-sync"}</span>
        </div>
      ) : null}
      <div className="flex items-center justify-around gap-2">
        <Gauge label="CPU" pct={d.cpu_usage ?? null} />
        <Gauge label="RAM" pct={d.mem_usage ?? null} />
        <div className="flex flex-col items-center justify-center w-20">
          <div className="text-lg font-semibold tabular-nums">
            {d.session_count != null ? d.session_count.toLocaleString() : "—"}
          </div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-wider">Sessions</div>
        </div>
      </div>
    </div>
  );
}

// threshold colours: <70 green, <90 amber, else red (CSS vars from globals.css).
function gaugeColor(pct: number): string {
  if (pct < 70) return "hsl(var(--chart-4))"; // green
  if (pct < 90) return "hsl(var(--chart-2))"; // amber
  return "hsl(var(--chart-3))"; // red
}

function Gauge({ label, pct }: { label: string; pct: number | null }) {
  const v = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const data = [{ value: v }, { value: 100 - v }];
  const color = gaugeColor(v);
  return (
    <div className="flex flex-col items-center w-20">
      <div className="relative w-16 h-16">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              startAngle={90}
              endAngle={-270}
              innerRadius="72%"
              outerRadius="100%"
              stroke="none"
              isAnimationActive={false}
            >
              <Cell fill={color} />
              <Cell fill="hsl(var(--muted))" />
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex items-center justify-center text-sm font-semibold tabular-nums">
          {pct == null ? "—" : `${Math.round(v)}%`}
        </div>
      </div>
      <div className="text-[10px] text-muted-foreground uppercase tracking-wider mt-0.5">{label}</div>
    </div>
  );
}
