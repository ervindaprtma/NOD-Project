"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatBytes, formatBucketLabelWIB, TIME_PRESETS } from "@/lib/constants";
import { AreaChart } from "@/components/charts/AreaChart";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";
import type { Envelope } from "./types";
import type { InterfaceStatsData, InterfaceStatsItem } from "@/types";

// WAN / MPLS bandwidth per site (restored from the old Resources → Interface
// Bandwidth tab). Reuses the untouched /api/v1/interface-stats endpoint, which
// returns the 4 hardcoded WAN/MPLS interfaces per site. Site is chosen one at a
// time (like the old page); the chooser is scoped to this tab only.
const SITES: { key: string; label: string }[] = [
  { key: "Site_FGT-DC", label: "DC" },
  { key: "Site_FGT-DRC", label: "DRC" },
  { key: "Site_FGT_Office", label: "Office" },
];

export function InterfaceBandwidth({ refreshMs = 0 }: { refreshMs?: number }) {
  const [site, setSite] = useState(SITES[0].key);
  // Default 15m (TIME_PRESETS[0]) — same as the old page.
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [gteMs, setGteMs] = useState(() => Date.now() - TIME_PRESETS[0].seconds * 1000);
  const [lteMs, setLteMs] = useState(() => Date.now());
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customRangeLabel, setCustomRangeLabel] = useState<string | null>(null);
  // Seconds of the active preset (0 = custom → window is fixed, never auto-slides).
  const [activeSeconds, setActiveSeconds] = useState(TIME_PRESETS[0].seconds);
  const label = SITES.find((s) => s.key === site)?.label ?? site;

  // Live viewport: on each refresh tick, slide a preset window forward (same as Traffic).
  // Custom ranges stay pinned. No tick when refresh is Off.
  useEffect(() => {
    if (refreshMs <= 0 || activeSeconds <= 0) return;
    const id = setInterval(() => {
      const now = Date.now();
      setGteMs(now - activeSeconds * 1000);
      setLteMs(now);
    }, refreshMs);
    return () => clearInterval(id);
  }, [refreshMs, activeSeconds]);

  function handlePreset(seconds: number, presetLabel: string) {
    const now = Date.now();
    setGteMs(now - seconds * 1000);
    setLteMs(now);
    setActiveSeconds(seconds);
    setSelectedPreset(presetLabel);
    setCustomRangeLabel(null);
    setShowCustomPicker(false);
  }

  function handleCustomApply(range: CustomTimeRange) {
    setGteMs(range.gte_ms);
    setLteMs(range.lte_ms);
    setActiveSeconds(0);
    setSelectedPreset("custom");
    setShowCustomPicker(false);
    const f = new Date(range.gte_ms), t = new Date(range.lte_ms);
    const fmt = (d: Date) =>
      `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}`;
    setCustomRangeLabel(`${fmt(f)} — ${fmt(t)}`);
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        {/* Site chooser (scoped to this tab) */}
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {SITES.map((s) => (
            <button key={s.key} onClick={() => setSite(s.key)}
              className={`px-3 py-1 transition ${site === s.key ? "bg-foreground text-background" : "hover:bg-muted/50"}`}>
              {s.label}
            </button>
          ))}
        </div>
        {/* Time-range presets + custom (same as old page) */}
        <div className="flex gap-1 bg-muted rounded-md p-1">
          {TIME_PRESETS.map((p) => (
            <button key={p.label} onClick={() => handlePreset(p.seconds, p.label)}
              className={cn("px-2.5 py-1 text-xs rounded-sm transition-colors",
                selectedPreset === p.label
                  ? "bg-background text-foreground shadow ring-1 ring-black/5 dark:ring-white/20"
                  : "text-muted-foreground hover:text-foreground hover:bg-background/50 dark:hover:bg-background/20")}>
              {p.label}
            </button>
          ))}
          <button onClick={() => setShowCustomPicker(true)}
            title={customRangeLabel || "Select custom date/time range"}
            className={cn("px-2.5 py-1 text-xs rounded-sm transition-colors",
              selectedPreset === "custom"
                ? "bg-background text-foreground shadow ring-1 ring-black/5 dark:ring-white/20"
                : "text-muted-foreground hover:text-foreground hover:bg-background/50 dark:hover:bg-background/20")}>
            {selectedPreset === "custom" && customRangeLabel
              ? customRangeLabel.length > 20 ? customRangeLabel.slice(0, 18) + "…" : customRangeLabel
              : "Custom"}
          </button>
        </div>
      </div>

      <TimeRangePicker
        isOpen={showCustomPicker}
        onApply={handleCustomApply}
        onCancel={() => setShowCustomPicker(false)}
        initialGteMs={gteMs}
        initialLteMs={lteMs}
      />

      <SiteBandwidth key={`${site}-${gteMs}-${lteMs}`} siteKey={site} label={label} gte={gteMs} lte={lteMs} />
    </div>
  );
}

function SiteBandwidth({ siteKey, label, gte, lte }: { siteKey: string; label: string; gte: number; lte: number }) {
  const { data, error, isLoading } = useSWR<Envelope<InterfaceStatsData>>(
    `/api/v1/interface-stats?site_name=${siteKey}&gte_ms=${gte}&lte_ms=${lte}`,
    swrFetcherLong,
    { revalidateOnFocus: false },
  );
  const ifs = data?.data?.interfaces ?? [];
  return (
    <div className="mb-6">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">{label}</h3>
      {isLoading ? (
        <p className="text-xs text-muted-foreground py-4">Loading…</p>
      ) : error ? (
        <p className="text-xs text-red-500 py-4">Failed to load interface stats for {label}.</p>
      ) : ifs.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4">No WAN/MPLS interfaces for {label}.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {ifs.map((iface, i) => <InterfaceBandwidthCard key={i} iface={iface} />)}
        </div>
      )}
    </div>
  );
}

function formatMbps(v: number | null): string {
  if (v == null) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)} Gbps` : `${v.toFixed(1)} Mbps`;
}

function InterfaceBandwidthCard({ iface }: { iface: InterfaceStatsItem }) {
  const isUp = iface.oper_status === 1;
  const chartData = (iface.timeline || []).map((pt, idx) => {
    const ms = pt.timestamp ? new Date(pt.timestamp).getTime() : 0;
    return {
      timestamp: ms ? formatBucketLabelWIB(ms, idx > 0 ? new Date(iface.timeline![idx - 1].timestamp).getTime() : null) : pt.timestamp,
      In: pt.in_mbps ?? null,   // null on a gap boundary → line breaks, no fake 0/spike
      Out: pt.out_mbps ?? null,
      tsMs: ms,
    };
  });
  const hasTimeline = chartData.length > 1;
  const bucketMs = chartData.length > 1 ? (chartData[1].tsMs - chartData[0].tsMs) || 60_000 : 60_000;
  const gapBands = (iface.gaps || []).flatMap((g) => {
    const x1 = chartData.find((d) => d.tsMs === g.start_ms)?.timestamp;
    const x2 = chartData.find((d) => d.tsMs === g.end_ms)?.timestamp;
    return x1 != null && x2 != null ? [{ x1, x2, label: "no data" }] : [];
  });

  return (
    <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <span className="text-sm font-semibold font-mono truncate" title={iface.label}>{iface.label}</span>
        <div className="flex items-center gap-2 shrink-0">
          {iface.speed_mbps != null && (
            <span className="inline-flex items-center rounded-md bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 px-2 py-0.5 text-[11px] font-medium">
              {iface.speed_mbps >= 1000 ? `${(iface.speed_mbps / 1000).toFixed(1)} Gbps` : `${iface.speed_mbps.toLocaleString()} Mbps`}
            </span>
          )}
          <span className={cn("inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium", isUp ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300" : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300")}>
            <span className={cn("inline-block w-1.5 h-1.5 rounded-full mr-1", isUp ? "bg-emerald-500" : "bg-red-500")} />
            {isUp ? "UP" : "DOWN"}
          </span>
        </div>
      </div>

      <div className="space-y-2">
        <div className="grid grid-cols-3 gap-2">
          {(["Avg", "Peak", "Last"] as const).map((h) => (
            <p key={h} className="text-[10px] text-muted-foreground uppercase tracking-wider text-center">{h}</p>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {[iface.avg_in_mbps, iface.peak_in_mbps, iface.last_in_mbps].map((v, i) => (
            <div key={i} className="bg-blue-50 dark:bg-blue-950/20 rounded-lg p-2 text-center">
              <p className="text-base font-bold text-blue-600 dark:text-blue-400">{formatMbps(v)}</p>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {[iface.avg_out_mbps, iface.peak_out_mbps, iface.last_out_mbps].map((v, i) => (
            <div key={i} className="bg-orange-50 dark:bg-orange-950/20 rounded-lg p-2 text-center">
              <p className="text-base font-bold text-orange-600 dark:text-orange-400">{formatMbps(v)}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="bg-blue-50/60 dark:bg-blue-950/10 rounded-lg p-2 text-center">
          <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Volume In</p>
          <p className="text-sm font-semibold text-blue-600 dark:text-blue-400">{formatBytes(iface.total_in_bytes)}</p>
        </div>
        <div className="bg-orange-50/60 dark:bg-orange-950/10 rounded-lg p-2 text-center">
          <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Volume Out</p>
          <p className="text-sm font-semibold text-orange-600 dark:text-orange-400">{formatBytes(iface.total_out_bytes)}</p>
        </div>
      </div>

      {hasTimeline ? (
        <div className="h-40 [&_text]:fill-gray-500 dark:[&_text]:fill-gray-400">
          <AreaChart
            className="h-full" data={chartData} index="timestamp" categories={["In", "Out"]}
            colors={["blue", "orange"]}
            valueFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)} Gbps` : `${v.toFixed(1)} Mbps`)}
            showLegend showXAxis showYAxis autoMinValue allowDecimals curveType="monotone"
            tickGap={30} yAxisWidth={60} bucketMs={bucketMs} bands={gapBands}
          />
        </div>
      ) : (
        <div className="bg-muted/30 rounded-lg h-40 flex items-center justify-center">
          <p className="text-xs text-muted-foreground">No timeline data available</p>
        </div>
      )}
    </div>
  );
}
