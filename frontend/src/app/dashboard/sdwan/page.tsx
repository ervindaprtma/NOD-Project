"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatMs, formatPercent, formatNumber, getDefaultTimeRange } from "@/lib/constants";
import type { SDWANData } from "@/types";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";

const SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"];

const SITE_BADGES: Record<string, string> = {
  "Site_FGT-DC": "DC",
  "Site_FGT-DRC": "DRC",
  "Site_FGT_Office": "Office",
};

const WAN_COLORS = ["#3b82f6", "#f59e0b"];
const MPLS_COLORS = ["#10b981", "#8b5cf6"];

type SectionId = "linkStatus" | "summary" | "wanLatency" | "mplsLatency" | "wanJitter" | "mplsJitter" | "wanLoss" | "mplsLoss";

export default function SDWANPage() {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [activePresetSeconds, setActivePresetSeconds] = useState(TIME_PRESETS[0].seconds);
  const [siteName, setSiteName] = useState("Site_FGT-DC");
  const [refreshInterval, setRefreshInterval] = useState(DEFAULT_REFRESH_MS);
  const [expanded, setExpanded] = useState<SectionId | null>(null);
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customRangeLabel, setCustomRangeLabel] = useState<string | null>(null);
  const prevIntervalRef = useRef(DEFAULT_REFRESH_MS);

  const token = typeof window !== "undefined" ? getAccessToken() : null;

  const [currentGteMs, setCurrentGteMs] = useState(defaultRange.gte_ms);
  const [currentLteMs, setCurrentLteMs] = useState(defaultRange.lte_ms);

  useEffect(() => {
    if (activePresetSeconds <= 0) {
      setCurrentGteMs(gteMs);
      setCurrentLteMs(lteMs);
      return;
    }
    const tick = () => {
      const now = Date.now();
      setCurrentGteMs(now - activePresetSeconds * 1000);
      setCurrentLteMs(now);
    };
    tick();
    const id = setInterval(tick, refreshInterval > 0 ? refreshInterval : 60_000);
    return () => clearInterval(id);
  }, [activePresetSeconds, refreshInterval, gteMs, lteMs]);

  const swrKey = token
    ? `/api/v1/sdwan/sla?site_name=${siteName}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}`
    : null;

  const { data, error, isLoading } = useSWR<{ data: SDWANData; meta: { query_took_ms: number } }>(
    swrKey,
    swrFetcher,
    { refreshInterval: 0 }
  );

  const sdwan = data?.data;
  const queryTook = data?.meta?.query_took_ms;
  const sourceIp = (data as any)?.meta?.source_ip || sdwan?.link_status?.[0]?.device || null;

  function handlePreset(seconds: number, label: string) {
    const now = Date.now();
    setGteMs(now - seconds * 1000);
    setLteMs(now);
    setActivePresetSeconds(seconds);
    setSelectedPreset(label);
    setCustomRangeLabel(null);
    setShowCustomPicker(false);
    // Restore auto-refresh if it was disabled by custom range
    setRefreshInterval(prev => prev === 0 ? prevIntervalRef.current : prev);
  }

  function handleCustomApply(range: CustomTimeRange) {
    setGteMs(range.gte_ms);
    setLteMs(range.lte_ms);
    setActivePresetSeconds(0);
    setSelectedPreset("custom");
    prevIntervalRef.current = refreshInterval > 0 ? refreshInterval : DEFAULT_REFRESH_MS;
    setRefreshInterval(0);
    setShowCustomPicker(false);
    const from = new Date(range.gte_ms);
    const to = new Date(range.lte_ms);
    setCustomRangeLabel(
      `${from.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${from.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })} — ${to.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${to.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}`
    );
  }

  function pointsByType(raw: { timestamp: number; value: number; label: string; link_type: string }[]) {
    const wan = raw.filter((p) => p.link_type === "WAN");
    const mpls = raw.filter((p) => p.link_type === "MPLS");
    const groups: Record<string, { timestamp: number; value: number }[]> = {};
    const uniqueLabels = [...new Set(raw.map((p) => p.label))];
    for (const label of uniqueLabels) {
      groups[label] = raw.filter((p) => p.label === label);
    }
    return { wan, mpls, groups, uniqueLabels };
  }

  const latGroups = sdwan ? pointsByType(sdwan.latency_timeline.links) : null;
  const jitGroups = sdwan ? pointsByType(sdwan.jitter_timeline.links) : null;
  const lossGroups = sdwan ? pointsByType(sdwan.packet_loss_timeline.links) : null;

  if (expanded) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setExpanded(null)}
            className="px-3 py-1.5 text-xs border rounded-md hover:bg-muted transition-colors"
          >
            ← Back to SD-WAN SLA
          </button>
          <h1 className="text-xl font-bold tracking-tight">
            {SECTION_LABELS[expanded]}
          </h1>
        </div>
        {expanded === "linkStatus" && renderLinkStatus(sdwan, isLoading)}
        {expanded === "summary" && renderSummary(sdwan)}
        {expanded.startsWith("wan") || expanded.startsWith("mpls") ? renderExpandedChart(expanded, latGroups, jitGroups, lossGroups, sdwan, isLoading) : null}
        <TimeRangePicker
          isOpen={showCustomPicker}
          onApply={handleCustomApply}
          onCancel={() => setShowCustomPicker(false)}
          initialGteMs={gteMs}
          initialLteMs={lteMs}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">SD-WAN Performance SLA</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={siteName}
            onChange={(e) => setSiteName(e.target.value)}
            className="px-3 py-1.5 rounded-md border bg-background text-sm"
          >
            {SITES.map((s) => (
              <option key={s} value={s}>{s.replace("_", " ")}</option>
            ))}
          </select>
          <span className={cn(
            "px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wide",
            siteName.includes("DC") && !siteName.includes("DRC") ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" :
            siteName.includes("DRC") ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" :
            "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
          )}>
            {SITE_BADGES[siteName] || siteName}
          </span>
          <div className="flex gap-1 bg-muted rounded-md p-1">
            {TIME_PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => handlePreset(p.seconds, p.label)}
                className={cn(
                  "px-2.5 py-1 text-xs rounded-sm transition-colors",
                  selectedPreset === p.label
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {p.label}
              </button>
            ))}
            <button
              onClick={() => setShowCustomPicker(true)}
              className={cn(
                "px-2.5 py-1 text-xs rounded-sm transition-colors",
                selectedPreset === "custom"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title={customRangeLabel || "Select custom date/time range"}
            >
              {selectedPreset === "custom" && customRangeLabel
                ? customRangeLabel.length > 20
                  ? customRangeLabel.slice(0, 18) + "…"
                  : customRangeLabel
                : "Custom"}
            </button>
          </div>
          <select
            value={refreshInterval}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="h-7 px-2 text-xs border rounded-md bg-card text-muted-foreground cursor-pointer"
          >
            {REFRESH_INTERVALS.map((ri) => (
              <option key={ri.value} value={ri.value}>
                {ri.label === "Off" ? "⏸ Off" : `↻ ${ri.label}`}
              </option>
            ))}
          </select>
          {queryTook != null && <span className="text-xs text-muted-foreground">{queryTook}ms</span>}
          {sourceIp && (
            <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400">
              Cluster: {sourceIp}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
          Failed to load SD-WAN data. <button onClick={() => window.location.reload()} className="underline">Retry</button>
        </div>
      )}

      {!isLoading && !error && !sdwan && (
        <div className="p-4 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300 text-sm">
          No SD-WAN SLA data returned for {siteName.replace(/_/g, " ")}. The device may be unreachable or has not reported metrics yet.
        </div>
      )}

      {/* Link Status Table */}
      <div className="bg-card border rounded-lg p-4 group">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">Link Status — {siteName.replace("_", " ")}</h2>
          <button
            onClick={() => setExpanded("linkStatus")}
            className="text-[11px] text-muted-foreground hover:text-primary transition-colors opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded hover:bg-muted"
          >
            View Full ↗
          </button>
        </div>
        {isLoading ? (
          <div className="space-y-2">{[1, 2, 3, 4].map((i) => <div key={i} className="h-8 bg-muted rounded animate-pulse" />)}</div>
        ) : sdwan?.link_status?.[0]?.links ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="text-left py-2">Type</th>
                <th className="text-left py-2">Label</th>
                <th className="text-left py-2">Interface</th>
                <th className="text-left py-2">Status</th>
                <th className="text-left py-2">SLA Target</th>
              </tr>
            </thead>
            <tbody>
              {sdwan.link_status[0].links.map((link, i) => (
                <tr key={i} className="border-b last:border-0">
                  <td className="py-2">
                    <span className={cn(
                      "px-2 py-0.5 rounded text-[11px] font-medium",
                      link.link_type === "WAN" ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400" :
                      "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                    )}>{link.link_type}</span>
                  </td>
                  <td className="py-2 font-medium">{link.label}</td>
                  <td className="py-2 font-mono text-xs">{link.ifname}</td>
                  <td className="py-2">
                    <span className={cn(
                      "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
                      link.status === "Up" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400" :
                      "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                    )}>
                      <span className={cn("w-1.5 h-1.5 rounded-full", link.status === "Up" ? "bg-emerald-500" : "bg-red-500")} />
                      {link.status}
                    </span>
                  </td>
                  <td className="py-2 text-muted-foreground text-xs">{link.sla_target}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground">No link status data</p>
        )}
      </div>

      {/* SLA Summary KPIs */}
      <SectionBlock title="SLA Summary KPIs" section="summary" onViewMore={() => setExpanded("summary")}>
        {sdwan?.summary ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {sdwan.summary.labels.map((label, i) => (
              <div key={i} className={cn(
                "bg-muted/50 border rounded-lg p-4",
                sdwan.summary.link_types[i] === "MPLS" && "border-l-4 border-l-emerald-500",
                sdwan.summary.link_types[i] === "WAN" && "border-l-4 border-l-blue-500"
              )}>
                <p className="text-xs text-muted-foreground mb-1 flex items-center gap-1.5">
                  <span className={cn(
                    "px-1 py-0.5 rounded text-[10px] font-medium",
                    sdwan.summary.link_types[i] === "WAN" ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" :
                    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                  )}>{sdwan.summary.link_types[i]}</span>
                  {label}
                </p>
                <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
                  <div>
                    <span className="text-[9px] text-muted-foreground">Lat</span>
                    <p className="text-sm font-bold">{formatMs(sdwan.summary.avg_latency[i])}</p>
                  </div>
                  <div>
                    <span className="text-[9px] text-muted-foreground">Max</span>
                    <p className="text-sm font-bold">{formatMs(sdwan.summary.max_latency[i])}</p>
                  </div>
                  <div>
                    <span className="text-[9px] text-muted-foreground">Jitter</span>
                    <p className="text-sm font-bold">{formatMs(sdwan.summary.avg_jitter[i])}</p>
                  </div>
                  <div>
                    <span className="text-[9px] text-muted-foreground">Loss</span>
                    <p className="text-sm font-bold">{formatPercent(sdwan.summary.avg_packet_loss[i])}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          !isLoading && <p className="text-sm text-muted-foreground text-center py-4">No summary data</p>
        )}
      </SectionBlock>

      {/* WAN Latency */}
      <MultiLinkChart
        title="WAN Latency (ms)" loading={isLoading} section="wanLatency" onViewMore={() => setExpanded("wanLatency")}
        groups={latGroups?.groups}
        wanLabels={latGroups?.uniqueLabels.filter((l) => sdwan?.latency_timeline.links.find((p) => p.label === l)?.link_type === "WAN") || []}
        colors={WAN_COLORS} format={formatMs}
      />

      {/* MPLS Latency */}
      <MultiLinkChart
        title="MPLS Latency (ms)" loading={isLoading} section="mplsLatency" onViewMore={() => setExpanded("mplsLatency")}
        groups={latGroups?.groups}
        wanLabels={latGroups?.uniqueLabels.filter((l) => sdwan?.latency_timeline.links.find((p) => p.label === l)?.link_type === "MPLS") || []}
        colors={MPLS_COLORS} format={formatMs}
      />

      {/* WAN Jitter */}
      <MultiLinkChart
        title="WAN Jitter (ms)" loading={isLoading} section="wanJitter" onViewMore={() => setExpanded("wanJitter")}
        groups={jitGroups?.groups}
        wanLabels={jitGroups?.uniqueLabels.filter((l) => sdwan?.jitter_timeline.links.find((p) => p.label === l)?.link_type === "WAN") || []}
        colors={WAN_COLORS} format={formatMs}
      />

      {/* MPLS Jitter */}
      <MultiLinkChart
        title="MPLS Jitter (ms)" loading={isLoading} section="mplsJitter" onViewMore={() => setExpanded("mplsJitter")}
        groups={jitGroups?.groups}
        wanLabels={jitGroups?.uniqueLabels.filter((l) => sdwan?.jitter_timeline.links.find((p) => p.label === l)?.link_type === "MPLS") || []}
        colors={MPLS_COLORS} format={formatMs}
      />

      {/* WAN Packet Loss */}
      <MultiLinkChart
        title="WAN Packet Loss (%)" loading={isLoading} section="wanLoss" onViewMore={() => setExpanded("wanLoss")}
        groups={lossGroups?.groups}
        wanLabels={lossGroups?.uniqueLabels.filter((l) => sdwan?.packet_loss_timeline.links.find((p) => p.label === l)?.link_type === "WAN") || []}
        colors={WAN_COLORS} format={formatPercent}
      />

      {/* MPLS Packet Loss */}
      <MultiLinkChart
        title="MPLS Packet Loss (%)" loading={isLoading} section="mplsLoss" onViewMore={() => setExpanded("mplsLoss")}
        groups={lossGroups?.groups}
        wanLabels={lossGroups?.uniqueLabels.filter((l) => sdwan?.packet_loss_timeline.links.find((p) => p.label === l)?.link_type === "MPLS") || []}
        colors={MPLS_COLORS} format={formatPercent}
      />

      <TimeRangePicker
        isOpen={showCustomPicker}
        onApply={handleCustomApply}
        onCancel={() => setShowCustomPicker(false)}
        initialGteMs={gteMs}
        initialLteMs={lteMs}
      />
    </div>
  );
}

// ── Section labels & expanded renderers ─────────────────────────

const SECTION_LABELS: Record<SectionId, string> = {
  linkStatus: "Link Status",
  summary: "SLA Summary KPIs",
  wanLatency: "WAN Latency",
  mplsLatency: "MPLS Latency",
  wanJitter: "WAN Jitter",
  mplsJitter: "MPLS Jitter",
  wanLoss: "WAN Packet Loss",
  mplsLoss: "MPLS Packet Loss",
};

function renderLinkStatus(sdwan: SDWANData | undefined, loading: boolean) {
  if (loading) return <div className="h-48 bg-muted animate-pulse rounded-lg" />;
  if (!sdwan?.link_status?.[0]?.links) return <p className="text-sm text-muted-foreground text-center py-12">No data</p>;
  return (
    <div className="bg-card border rounded-lg p-6">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-muted-foreground text-left">
            <th className="py-3">Type</th><th className="py-3">Label</th><th className="py-3">Interface</th>
            <th className="py-3">Status</th><th className="py-3">SLA Target</th>
          </tr>
        </thead>
        <tbody>
          {sdwan.link_status[0].links.map((link, i) => (
            <tr key={i} className="border-b last:border-0">
              <td className="py-3">
                <span className={cn("px-2 py-0.5 rounded text-[11px] font-medium",
                  link.link_type === "WAN" ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400" :
                  "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                )}>{link.link_type}</span>
              </td>
              <td className="py-3 font-medium">{link.label}</td>
              <td className="py-3 font-mono text-xs">{link.ifname}</td>
              <td className="py-3">
                <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
                  link.status === "Up" ? "badge-success" : "badge-danger"
                )}>
                  <span className={cn("w-1.5 h-1.5 rounded-full", link.status === "Up" ? "bg-emerald-500" : "bg-red-500")} />
                  {link.status}
                </span>
              </td>
              <td className="py-3 text-muted-foreground text-xs">{link.sla_target}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderSummary(sdwan: SDWANData | undefined) {
  if (!sdwan?.summary) return <p className="text-sm text-muted-foreground text-center py-12">No data</p>;
  return (
    <div className="bg-card border rounded-lg p-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {sdwan.summary.labels.map((label, i) => (
          <div key={i} className={cn("bg-muted/50 border rounded-lg p-4",
            sdwan.summary.link_types[i] === "MPLS" && "border-l-4 border-l-emerald-500",
            sdwan.summary.link_types[i] === "WAN" && "border-l-4 border-l-blue-500"
          )}>
            <p className="text-xs text-muted-foreground mb-1">{sdwan.summary.link_types[i]} · {label}</p>
            <div className="grid grid-cols-2 gap-2">
              <div><span className="text-[9px] text-muted-foreground">Lat</span><p className="text-lg font-bold">{formatMs(sdwan.summary.avg_latency[i])}</p></div>
              <div><span className="text-[9px] text-muted-foreground">Max</span><p className="text-lg font-bold">{formatMs(sdwan.summary.max_latency[i])}</p></div>
              <div><span className="text-[9px] text-muted-foreground">Jitter</span><p className="text-lg font-bold">{formatMs(sdwan.summary.avg_jitter[i])}</p></div>
              <div><span className="text-[9px] text-muted-foreground">Loss</span><p className="text-lg font-bold">{formatPercent(sdwan.summary.avg_packet_loss[i])}</p></div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function renderExpandedChart(
  section: SectionId,
  latGroups: any, jitGroups: any, lossGroups: any,
  sdwan: SDWANData | undefined, loading: boolean
) {
  if (loading) return <div className="h-64 bg-muted animate-pulse rounded-lg" />;
  let groups: any; let wanLabels: string[]; let colors: string[]; let formatFn = formatMs; let title = SECTION_LABELS[section];
  const raw = section.startsWith("wan") ? sdwan?.[
    section.includes("Latency") ? "latency_timeline" : section.includes("Jitter") ? "jitter_timeline" : "packet_loss_timeline"
  ] : sdwan?.[
    section.includes("Latency") ? "latency_timeline" : section.includes("Jitter") ? "jitter_timeline" : "packet_loss_timeline"
  ];
  // Simplified: use the groups from the main component
  if (!sdwan) return <p className="text-sm text-muted-foreground text-center py-12">No data</p>;
  const typedSdwan = sdwan as any;
  let links: { timestamp: number; value: number; label: string; link_type: string }[] = [];
  if (section.includes("Latency")) links = typedSdwan?.latency_timeline?.links || [];
  else if (section.includes("Jitter")) links = typedSdwan?.jitter_timeline?.links || [];
  else links = typedSdwan?.packet_loss_timeline?.links || [];
  const typeFilter = section.startsWith("wan") ? "WAN" : "MPLS";
  const filtered = links.filter((l: any) => l.link_type === typeFilter);
  if (filtered.length === 0) return <p className="text-sm text-muted-foreground text-center py-12">No data for this metric</p>;
  const byLabel: Record<string, { timestamp: number; value: number }[]> = {};
  for (const p of filtered) { if (!byLabel[p.label]) byLabel[p.label] = []; byLabel[p.label].push(p); }
  const labels = Object.keys(byLabel);
  if (section.includes("Loss")) formatFn = formatPercent;
  const c = typeFilter === "WAN" ? WAN_COLORS : MPLS_COLORS;
  return (
    <div className="bg-card border rounded-lg p-6">
      <MultiLinkChartInner title={title} groups={byLabel} wanLabels={labels} colors={c} format={formatFn} />
    </div>
  );
}

// ── Section wrapper ─────────────────────────────────────────────

function SectionBlock({
  title, section, onViewMore, children,
}: { title: string; section: string; onViewMore: () => void; children: React.ReactNode }) {
  return (
    <div className="bg-card border rounded-lg p-4 group">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        <button
          onClick={onViewMore}
          className="text-[11px] text-muted-foreground hover:text-primary transition-colors opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded hover:bg-muted"
        >
          View Full ↗
        </button>
      </div>
      {children}
    </div>
  );
}

// ── Multi-Link Timeline Chart ───────────────────────────────────

function MultiLinkChart({
  title, loading, section, onViewMore,
  groups, wanLabels, colors, format,
}: {
  title: string; loading: boolean; section: string; onViewMore: () => void;
  groups?: Record<string, { timestamp: number; value: number }[]>;
  wanLabels: string[]; colors: string[]; format: (v: number) => string;
}) {
  return (
    <div className="bg-card border rounded-lg p-4 group">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        <button
          onClick={onViewMore}
          className="text-[11px] text-muted-foreground hover:text-primary transition-colors opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded hover:bg-muted"
        >
          View Full ↗
        </button>
      </div>
      {loading ? (
        <div className="h-48 bg-muted animate-pulse rounded" />
      ) : (
        <MultiLinkChartInner title={title} groups={groups} wanLabels={wanLabels} colors={colors} format={format} />
      )}
    </div>
  );
}

function MultiLinkChartInner({
  title, groups, wanLabels, colors, format,
}: {
  title: string;
  groups?: Record<string, { timestamp: number; value: number }[]>;
  wanLabels: string[]; colors: string[]; format: (v: number) => string;
}) {
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  if (!groups || !wanLabels || wanLabels.length === 0) {
    return <p className="text-xs text-muted-foreground text-center py-8">No data</p>;
  }
  const displayLabels = wanLabels.filter((l) => visible[l] !== false);
  if (displayLabels.length === 0) {
    wanLabels.forEach((l) => { if (visible[l] === undefined) setVisible(prev => ({ ...prev, [l]: true })); });
  }
  const finalLabels = wanLabels.filter((l) => visible[l] !== false);
  const allValues = wanLabels.flatMap((l) => (groups[l] || []).map((d) => d.value));
  const min = Math.min(...allValues, 0);
  const max = Math.max(...allValues, 1);
  const range = max - min || 1;
  const W = 700; const H = 220; const pad = { top: 10, right: 20, bottom: 30, left: 65 };
  const allTimestamps = [...new Set(wanLabels.flatMap((l) => (groups[l] || []).map((d) => d.timestamp)))].sort((a, b) => a - b);
  function xScale(i: number) { return pad.left + (i / (allTimestamps.length - 1 || 1)) * (W - pad.left - pad.right); }
  function yScale(v: number) { return pad.top + (1 - (v - min) / range) * (H - pad.top - pad.bottom); }

  return (
    <div>
      <div className="flex items-center gap-3 mb-2 flex-wrap">
        {wanLabels.map((label, i) => (
          <label key={label} className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="checkbox" checked={visible[label] !== false}
              onChange={() => setVisible(prev => ({ ...prev, [label]: prev[label] === false ? true : false }))}
              className="rounded" />
            <span style={{ color: colors[i % colors.length] }}>{label}</span>
          </label>
        ))}
      </div>
      {allTimestamps.length === 0 ? (
        <p className="text-xs text-muted-foreground text-center py-8">No data for selected time range</p>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 240 }}>
          {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
            const y = yScale(min + frac * range);
            return (
              <g key={frac}>
                <line x1={pad.left} x2={W - pad.right} y1={y} y2={y} className="stroke-muted-foreground/15" strokeWidth={0.5} />
                <text x={pad.left - 6} y={y + 4} textAnchor="end" className="text-[10px] fill-muted-foreground">
                  {format(min + frac * range)}
                </text>
              </g>
            );
          })}
          {finalLabels.map((label, idx) => {
            const pts = groups[label] || [];
            if (pts.length < 2) return null;
            const points = pts.map((d) => {
              const i = allTimestamps.indexOf(d.timestamp);
              return `${xScale(i)},${yScale(d.value)}`;
            }).join(" ");
            return <polyline key={label} points={points} fill="none"
              stroke={colors[idx % colors.length]} strokeWidth={2} />;
          })}
          {allTimestamps.length > 0 && (
            <>
              <text x={pad.left} y={H - 8} textAnchor="start" className="text-[10px] fill-muted-foreground">
                {new Date(allTimestamps[0]).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" })}
              </text>
              <text x={W - pad.right} y={H - 8} textAnchor="end" className="text-[10px] fill-muted-foreground">
                {new Date(allTimestamps[allTimestamps.length - 1]).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" })}
              </text>
            </>
          )}
        </svg>
      )}
    </div>
  );
}
