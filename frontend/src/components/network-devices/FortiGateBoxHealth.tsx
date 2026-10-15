"use client";

import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import { formatBucketLabelWIB } from "@/lib/constants";
import { AreaChart } from "@/components/charts/AreaChart";
import { ChartFrame } from "./ChartFrame";
import type { Envelope } from "./types";
import { FGT_SITE_NAME } from "./types";
import type { ResourceData } from "@/types";

// FGT-only box-health (CPU / Mem / Sessions / HA). Reuses the existing /resources
// endpoint — switches have no such telemetry, so this renders only for FortiGate.
// Current-value tiles + CPU%/Mem%/Sessions trend charts over the selected window.
const WINDOW_SEC: Record<string, number> = { "12h": 43200, "24h": 86400, "7d": 604800, "30d": 2592000 };

export function FortiGateBoxHealth({ site, window = "24h", gteMs, lteMs, refreshMs = 60_000, onRangeSelect }: { site: string | null; window?: string; gteMs?: number; lteMs?: number; refreshMs?: number; onRangeSelect?: (gteMs: number, lteMs: number) => void }) {
  const siteName = site ? FGT_SITE_NAME[site] : undefined;
  // /resources requires gte_ms/lte_ms (no window param). Custom range wins; otherwise
  // derive from the preset, rounding to the minute so the SWR key is stable across
  // renders (a raw Date.now() refetches endlessly → stuck loading).
  const lte = lteMs ?? Math.floor(Date.now() / 60_000) * 60_000;
  const gte = gteMs ?? lte - (WINDOW_SEC[window] ?? 86400) * 1000;
  const { data, error, isLoading } = useSWR<Envelope<ResourceData>>(
    siteName ? `/api/v1/resources?site_name=${siteName}&gte_ms=${gte}&lte_ms=${lte}` : null,
    siteName ? (url: string) => swrFetcherLong(url) : null,
    { revalidateOnFocus: false, refreshInterval: refreshMs },
  );

  if (!siteName) return null;

  const current = data?.data.current || [];
  const tl = data?.data.timeline;

  return (
    <div className="rounded-md border border-border p-3 mb-4">
      <h3 className="text-sm font-semibold mb-3">Box Health</h3>
      {isLoading ? (
        <p className="text-xs text-muted-foreground">Loading box health…</p>
      ) : error ? (
        <p className="text-xs text-red-500">Failed to load box health.</p>
      ) : current.length === 0 ? (
        <p className="text-xs text-muted-foreground">No resource data.</p>
      ) : (
        <div className="space-y-4">
          <div className="space-y-3">
            {current.map((d) => (
              <div key={d.serial_number || d.hostname || d.device}>
                {current.length > 1 ? (
                  <div className="text-xs font-medium mb-1">
                    {d.hostname || d.device}
                    {d.sync_status ? (
                      <span className="ml-2 text-muted-foreground">
                        HA: {d.sync_status === "1" || d.sync_status.toLowerCase().includes("sync") ? "● in-sync" : "○ out-of-sync"}
                      </span>
                    ) : null}
                  </div>
                ) : null}
                <div className="grid grid-cols-3 gap-2">
                  <Tile label="CPU" value={pct(d.cpu_usage)} />
                  <Tile label="Memory" value={pct(d.mem_usage)} />
                  <Tile label="Sessions" value={d.session_count != null ? d.session_count.toLocaleString() : "—"} />
                </div>
              </div>
            ))}
          </div>

          {tl ? (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <MetricChart title="CPU %" pts={tl.cpu} fmt={(v) => `${v.toFixed(0)}%`} onRangeSelect={onRangeSelect} />
              <MetricChart title="Memory %" pts={tl.memory} fmt={(v) => `${v.toFixed(0)}%`} onRangeSelect={onRangeSelect} />
              <MetricChart title="Sessions" pts={tl.sessions} fmt={(v) => v.toLocaleString()} onRangeSelect={onRangeSelect} />
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

type Pt = { timestamp: number; value: number; device: string };

// Pivot a flat [{timestamp,value,device}] series (HA interleaves 2 devices) into
// recharts rows with one column per device, so each HA member draws its own line.
function pivot(pts: Pt[]): { rows: Record<string, unknown>[]; devices: string[] } {
  const devices: string[] = [];
  const byTs = new Map<number, Record<string, unknown>>();
  for (const p of pts) {
    if (!devices.includes(p.device)) devices.push(p.device);
    let row = byTs.get(p.timestamp);
    if (!row) { row = { tsMs: p.timestamp }; byTs.set(p.timestamp, row); }
    row[p.device] = p.value;
  }
  const sorted = [...byTs.values()].sort((a, b) => (a.tsMs as number) - (b.tsMs as number));
  const rows = sorted.map((r, i) => ({
    ...r,
    t: formatBucketLabelWIB(r.tsMs as number, i > 0 ? (sorted[i - 1].tsMs as number) : null),
  }));
  return { rows, devices };
}

function MetricChart({ title, pts, fmt, onRangeSelect }: { title: string; pts: Pt[]; fmt: (v: number) => string; onRangeSelect?: (gteMs: number, lteMs: number) => void }) {
  const { rows, devices } = pivot(pts || []);
  const bucketMs = rows.length > 1 ? ((rows[1].tsMs as number) - (rows[0].tsMs as number)) || 60_000 : 60_000;
  return (
    <div>
      <p className="text-[11px] text-muted-foreground uppercase tracking-wider mb-1">{title}</p>
      {rows.length > 1 ? (
        <ChartFrame title={title} inlineClassName="h-40">
          <div className="h-full [&_text]:fill-gray-500 dark:[&_text]:fill-gray-400">
            <AreaChart
              className="h-full" data={rows} index="t" categories={devices}
              colors={["blue", "orange"]} valueFormatter={fmt}
              showLegend={devices.length > 1} showXAxis showYAxis autoMinValue allowDecimals
              curveType="monotone" tickGap={40} yAxisWidth={50} bucketMs={bucketMs}
              onRangeSelect={onRangeSelect}
            />
          </div>
        </ChartFrame>
      ) : (
        <div className="bg-muted/30 rounded-lg h-40 flex items-center justify-center">
          <p className="text-xs text-muted-foreground">No timeline data</p>
        </div>
      )}
    </div>
  );
}

function pct(v: number | undefined): string {
  return v != null ? `${v.toFixed(0)}%` : "—";
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/40 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}
