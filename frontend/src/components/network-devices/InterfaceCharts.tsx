"use client";

import { useState } from "react";
import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import { formatBucketLabelWIB } from "@/lib/constants";
import { AreaChart } from "@/components/charts/AreaChart";
import { ChartFrame } from "./ChartFrame";
import type { ChartData, Envelope, InterfaceBox } from "./types";
import { fmtSpeed } from "./types";

type Metric = "mb" | "mbps";

export function InterfaceCharts({
  source,
  iface,
  window,
  gteMs,
  lteMs,
  refreshMs = 0,
  onRangeSelect,
}: {
  source: string;
  iface: InterfaceBox;
  window: string;
  gteMs?: number;
  lteMs?: number;
  refreshMs?: number;
  onRangeSelect?: (gteMs: number, lteMs: number) => void;  // drag-to-zoom
}) {
  const [metric, setMetric] = useState<Metric>("mbps");

  // Custom range overrides the preset window (backend accepts gte_ms/lte_ms).
  const range = gteMs != null && lteMs != null ? `gte_ms=${gteMs}&lte_ms=${lteMs}` : `window=${window}`;
  // ifindex-keyed so interface names with "/" never hit the URL; current one is safe.
  const key = iface.ifindex
    ? `/api/v1/network-devices/devices/${encodeURIComponent(source)}/interfaces/${encodeURIComponent(
        iface.ifindex,
      )}/chart?metric=${metric}&${range}`
    : null;

  const { data, error, isLoading } = useSWR<Envelope<ChartData>>(key, swrFetcherLong, {
    revalidateOnFocus: false,
    refreshInterval: refreshMs,
  });

  const chart = data?.data;
  const pts = chart?.points || [];
  // Date+time x-axis labels (WIB), same as the WAN-MPLS Bandwidth chart — the label shows
  // the date on a day boundary and the time otherwise.
  const rows = pts.map((p, idx) => ({
    tsMs: p.ts_ms,
    time: formatBucketLabelWIB(p.ts_ms, idx > 0 ? pts[idx - 1].ts_ms : null),
    In: p.in,
    Out: p.out,
    ...(p.util_pct != null ? { "Util %": p.util_pct } : {}),
  }));

  const unit = metric === "mb" ? "MB" : "Mbps";
  const categories = metric === "mbps" && chart?.speed_mbps ? ["In", "Out", "Util %"] : ["In", "Out"];
  // Drag-to-zoom maps the dragged pixels to epoch bounds via each datum's tsMs + bucket width.
  const bucketMs = rows.length > 1 ? (rows[1].tsMs - rows[0].tsMs) || 60_000 : 60_000;

  return (
    <div className="mt-2 rounded-md border border-border p-3 bg-muted/20">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-medium">
          {iface.if_name}{" "}
          <span className="text-xs text-muted-foreground">
            {iface.state} · {fmtSpeed(iface.speed_mbps)}
            {iface.description ? ` · ${iface.description}` : ""}
          </span>
        </div>
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {(["mb", "mbps"] as Metric[]).map((m) => (
            <button
              key={m}
              onClick={() => setMetric(m)}
              className={`px-2.5 py-1 transition ${
                metric === m ? "bg-foreground text-background" : "hover:bg-muted/50"
              }`}
            >
              {m === "mb" ? "Volume (MB)" : "Throughput (Mbps)"}
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <p className="text-xs text-muted-foreground py-8 text-center">Loading chart…</p>
      ) : error ? (
        <p className="text-xs text-red-500 py-8 text-center">Failed to load chart.</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground py-8 text-center">No traffic in window.</p>
      ) : (
        <ChartFrame title={`${iface.if_name} — ${metric === "mb" ? "Volume (MB)" : "Throughput (Mbps)"}`} inlineClassName="h-56">
          <AreaChart
            data={rows}
            index="time"
            categories={categories}
            valueFormatter={(v) => `${v.toFixed(2)} ${unit}`}
            showLegend
            className="h-full"
            bucketMs={bucketMs}
            onRangeSelect={onRangeSelect}
          />
        </ChartFrame>
      )}
      {metric === "mbps" && chart && !chart.speed_mbps ? (
        <p className="text-[11px] text-muted-foreground mt-1">
          Link speed unknown — utilization % hidden.
        </p>
      ) : null}
    </div>
  );
}
