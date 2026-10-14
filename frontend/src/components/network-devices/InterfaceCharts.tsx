"use client";

import { useState } from "react";
import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import { AreaChart } from "@/components/charts/AreaChart";
import type { ChartData, Envelope, InterfaceBox } from "./types";
import { fmtSpeed } from "./types";

type Metric = "mb" | "mbps";

export function InterfaceCharts({
  source,
  iface,
  window,
}: {
  source: string;
  iface: InterfaceBox;
  window: string;
}) {
  const [metric, setMetric] = useState<Metric>("mbps");

  // ifindex-keyed so interface names with "/" never hit the URL; current one is safe.
  const key = iface.ifindex
    ? `/api/v1/network-devices/devices/${encodeURIComponent(source)}/interfaces/${encodeURIComponent(
        iface.ifindex,
      )}/chart?metric=${metric}&window=${window}`
    : null;

  const { data, error, isLoading } = useSWR<Envelope<ChartData>>(key, swrFetcherLong, {
    revalidateOnFocus: false,
  });

  const chart = data?.data;
  const rows = (chart?.points || []).map((p) => ({
    tsMs: p.ts_ms,
    time: new Date(p.ts_ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }),
    In: p.in,
    Out: p.out,
    ...(p.util_pct != null ? { "Util %": p.util_pct } : {}),
  }));

  const unit = metric === "mb" ? "MB" : "Mbps";
  const categories = metric === "mbps" && chart?.speed_mbps ? ["In", "Out", "Util %"] : ["In", "Out"];

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
        <AreaChart
          data={rows}
          index="time"
          categories={categories}
          valueFormatter={(v) => `${v.toFixed(2)} ${unit}`}
          showLegend
          className="h-56"
        />
      )}
      {metric === "mbps" && chart && !chart.speed_mbps ? (
        <p className="text-[11px] text-muted-foreground mt-1">
          Link speed unknown — utilization % hidden.
        </p>
      ) : null}
    </div>
  );
}
