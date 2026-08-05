"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatMs, formatPercent, formatNumber, getDefaultTimeRange, formatBucketLabelWIB } from "@/lib/constants";
import type { SDWANData } from "@/types";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";
import { AreaChart } from "@/components/charts/AreaChart";

const SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"];

const SITE_BADGES: Record<string, string> = {
  "Site_FGT-DC": "DC",
  "Site_FGT-DRC": "DRC",
  "Site_FGT_Office": "Office",
};

type SectionId = "linkStatus" | "summary" | "latency" | "jitter" | "packetLoss";

export default function SDWANPage() {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [activePresetSeconds, setActivePresetSeconds] = useState(TIME_PRESETS[0].seconds);
  const [siteName, setSiteName] = useState("Site_FGT-DC");
  // Pre-select the site when navigated from Overview's per-site SD-WAN block with ?site=…
  // (client-only; a useState initializer reading the URL would hydration-mismatch the SSR default).
  useEffect(() => {
    const s = new URLSearchParams(window.location.search).get("site");
    if (s && SITES.includes(s)) setSiteName(s);
  }, []);
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

  // ── Link filter (visual): which links to show across every table & chart ──
  const allLabels = sdwan?.summary?.labels ?? [];
  const allTypes = sdwan?.summary?.link_types ?? [];
  const linkMeta = allLabels.map((l, i) => ({ label: l, type: allTypes[i] || "WAN" }));
  const [selectedLinks, setSelectedLinks] = useState<Set<string>>(new Set());
  // Self-correcting default: an untouched or stale (other-site) selection shows all links.
  const effectiveLinks =
    selectedLinks.size && allLabels.some((l) => selectedLinks.has(l))
      ? selectedLinks
      : new Set(allLabels);
  const isLinkVisible = (label: string) => effectiveLinks.has(label);
  function toggleLink(label: string) {
    setSelectedLinks((prev) => {
      const base = prev.size && allLabels.some((l) => prev.has(l)) ? prev : new Set(allLabels);
      const next = new Set(base);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }
  const selectLinks = (labels: string[]) => setSelectedLinks(new Set(labels));

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

  // Drag-to-zoom on any SLA chart narrows the page's time range (same as Traffic).
  const [isZoomed, setIsZoomed] = useState(false);
  const preZoomRef = useRef<{ gteMs: number; lteMs: number; activePresetSeconds: number; selectedPreset: string; customRangeLabel: string | null; refreshInterval: number } | null>(null);

  function applyBrushRange(g: number, l: number) {
    if (!isZoomed) {
      preZoomRef.current = { gteMs, lteMs, activePresetSeconds, selectedPreset, customRangeLabel, refreshInterval };
      setIsZoomed(true);
    }
    handleCustomApply({ gte_ms: g, lte_ms: l });
  }

  function resetZoom() {
    const s = preZoomRef.current;
    setIsZoomed(false);
    if (!s) return;
    setActivePresetSeconds(s.activePresetSeconds);
    setSelectedPreset(s.selectedPreset);
    setCustomRangeLabel(s.customRangeLabel);
    setRefreshInterval(s.refreshInterval);
    setGteMs(s.gteMs);
    setLteMs(s.lteMs);
  }

  // Data is directly available from API — no grouping needed

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
        {expanded === "linkStatus" && renderLinkStatus(sdwan, isLoading, isLinkVisible)}
        {expanded === "summary" && renderSummary(sdwan, isLinkVisible)}
        {(expanded === "latency" || expanded === "jitter" || expanded === "packetLoss") &&
          renderExpandedChart(expanded, sdwan, isLoading, applyBrushRange, isLinkVisible)}
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
          {isZoomed && (
            <button
              onClick={resetZoom}
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted/50 transition-colors"
              title="Restore the view before drag-zoom"
            >
              ⟲ Reset zoom
            </button>
          )}
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

      {/* Link filter bar */}
      {linkMeta.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap bg-card border rounded-lg px-3 py-2">
          <span className="text-xs font-medium text-muted-foreground">Links</span>
          <div className="flex gap-1">
            {[
              { label: "All", labels: allLabels },
              { label: "WAN", labels: linkMeta.filter((m) => m.type === "WAN").map((m) => m.label) },
              { label: "MPLS", labels: linkMeta.filter((m) => m.type === "MPLS").map((m) => m.label) },
            ].filter((q) => q.labels.length > 0).map((q) => (
              <button
                key={q.label}
                onClick={() => selectLinks(q.labels)}
                className="px-2 py-0.5 text-[11px] rounded border bg-background text-muted-foreground hover:bg-muted transition-colors"
              >
                {q.label}
              </button>
            ))}
          </div>
          <div className="h-4 w-px bg-border mx-0.5" />
          <div className="flex gap-1.5 flex-wrap">
            {linkMeta.map((m) => {
              const on = isLinkVisible(m.label);
              const isWan = m.type === "WAN";
              return (
                <button
                  key={m.label}
                  onClick={() => toggleLink(m.label)}
                  className={cn(
                    "inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] rounded-full border transition-colors",
                    on
                      ? isWan
                        ? "bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800"
                        : "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800"
                      : "bg-background text-muted-foreground border-border opacity-60 line-through"
                  )}
                  title={on ? "Click to hide" : "Click to show"}
                >
                  <span className={cn("w-1.5 h-1.5 rounded-full", isWan ? "bg-blue-500" : "bg-emerald-500", !on && "opacity-40")} />
                  {m.label}
                </button>
              );
            })}
          </div>
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
              {sdwan.link_status[0].links.filter((link) => isLinkVisible(link.label)).map((link, i) => (
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
            {sdwan.summary.labels.map((label, i) => !isLinkVisible(label) ? null : (
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
                    <p className="text-sm font-bold">{formatMs(sdwan.summary.avg_jitter[i], true)}</p>
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

      {/* Latency (all links) */}
      <SlaTimeseriesChart
        title="Latency (ms)" loading={isLoading} section="latency"
        onViewMore={() => setExpanded("latency")}
        links={sdwan?.latency_timeline?.links?.filter((l) => isLinkVisible(l.label))} color="blue" format={formatMs}
        onRangeSelect={applyBrushRange}
      />

      {/* Jitter (all links) */}
      <SlaTimeseriesChart
        title="Jitter (ms)" loading={isLoading} section="jitter"
        onViewMore={() => setExpanded("jitter")}
        links={sdwan?.jitter_timeline?.links?.filter((l) => isLinkVisible(l.label))} color="orange" format={(v: number) => formatMs(v, true)}
        onRangeSelect={applyBrushRange}
      />

      {/* Packet Loss (all links) */}
      <SlaTimeseriesChart
        title="Packet Loss (%)" loading={isLoading} section="packetLoss"
        onViewMore={() => setExpanded("packetLoss")}
        links={sdwan?.packet_loss_timeline?.links?.filter((l) => isLinkVisible(l.label))} color="red" format={formatPercent}
        onRangeSelect={applyBrushRange}
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
  latency: "Latency",
  jitter: "Jitter",
  packetLoss: "Packet Loss",
};

function renderLinkStatus(sdwan: SDWANData | undefined, loading: boolean, visible?: (label: string) => boolean) {
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
          {sdwan.link_status[0].links.filter((link) => !visible || visible(link.label)).map((link, i) => (
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

function renderSummary(sdwan: SDWANData | undefined, visible?: (label: string) => boolean) {
  if (!sdwan?.summary) return <p className="text-sm text-muted-foreground text-center py-12">No data</p>;
  return (
    <div className="bg-card border rounded-lg p-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {sdwan.summary.labels.map((label, i) => (visible && !visible(label)) ? null : (
          <div key={i} className={cn("bg-muted/50 border rounded-lg p-4",
            sdwan.summary.link_types[i] === "MPLS" && "border-l-4 border-l-emerald-500",
            sdwan.summary.link_types[i] === "WAN" && "border-l-4 border-l-blue-500"
          )}>
            <p className="text-xs text-muted-foreground mb-1">{sdwan.summary.link_types[i]} · {label}</p>
            <div className="grid grid-cols-2 gap-2">
              <div><span className="text-[9px] text-muted-foreground">Lat</span><p className="text-lg font-bold">{formatMs(sdwan.summary.avg_latency[i])}</p></div>
              <div><span className="text-[9px] text-muted-foreground">Max</span><p className="text-lg font-bold">{formatMs(sdwan.summary.max_latency[i])}</p></div>
              <div><span className="text-[9px] text-muted-foreground">Jitter</span><p className="text-lg font-bold">{formatMs(sdwan.summary.avg_jitter[i], true)}</p></div>
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
  sdwan: SDWANData | undefined, loading: boolean,
  onRangeSelect?: (gteMs: number, lteMs: number) => void,
  visible?: (label: string) => boolean,
) {
  if (loading) return <div className="h-64 bg-muted animate-pulse rounded-lg" />;
  if (!sdwan) return <p className="text-sm text-muted-foreground text-center py-12">No data</p>;

  const flt = (links: { label: string }[]) => (visible ? links.filter((l) => visible(l.label)) : links);
  const metricMap: Record<string, { links: { timestamp: number; value: number; label: string; link_type: string }[]; color: string; format: (v: number) => string }> = {
    latency: { links: flt(sdwan.latency_timeline?.links || []) as any, color: "blue", format: formatMs },
    jitter: { links: flt(sdwan.jitter_timeline?.links || []) as any, color: "orange", format: (v: number) => formatMs(v, true) },
    packetLoss: { links: flt(sdwan.packet_loss_timeline?.links || []) as any, color: "red", format: formatPercent },
  };

  const metric = metricMap[section];
  if (!metric || metric.links.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-12">No data for this metric</p>;
  }

  return (
    <div className="bg-card border rounded-lg p-6">
      <div className="h-64 [&_text]:fill-gray-500 dark:[&_text]:fill-gray-400">
        <SlaAreaChart links={metric.links} color={metric.color} format={metric.format} onRangeSelect={onRangeSelect} />
      </div>
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

// ── SLA Timeseries Chart (compact card) ────────────────────────

function SlaTimeseriesChart({
  title, loading, section, onViewMore, links, color, format, onRangeSelect,
}: {
  title: string; loading: boolean; section: string; onViewMore: () => void;
  links?: { timestamp: number; value: number; label: string; link_type: string }[];
  color: string; format: (v: number) => string;
  onRangeSelect?: (gteMs: number, lteMs: number) => void;
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
      ) : links && links.length > 0 ? (
        <div className="h-48 [&_text]:fill-gray-500 dark:[&_text]:fill-gray-400">
          <SlaAreaChart links={links} color={color} format={format} onRangeSelect={onRangeSelect} />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground text-center py-8">No data</p>
      )}
    </div>
  );
}

// ── SLA Area Chart (Tremor) ────────────────────────────────────

function SlaAreaChart({
  links, color, format, onRangeSelect,
}: {
  links: { timestamp: number; value: number; label: string; link_type: string }[];
  color: string; format: (v: number) => string;
  onRangeSelect?: (gteMs: number, lteMs: number) => void;
}) {
  const labels = [...new Set(links.map(l => l.label))];

  // Build Tremor-compatible data: each timestamp row has one value per link label
  const byTs: Record<number, Record<string, number>> = {};
  for (const p of links) {
    if (!byTs[p.timestamp]) byTs[p.timestamp] = {};
    byTs[p.timestamp][p.label] = p.value;
  }
  const chartData = Object.entries(byTs)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([ts, vals], idx, arr) => {
      const ms = Number(ts);
      const prevMs = idx > 0 ? Number(arr[idx - 1][0]) : null;
      return {
        // Use the Resources-page formatter: always show date on the first row
        // and on any day boundary; subsequent rows in the same day show time only.
        timestamp: formatBucketLabelWIB(ms, prevMs),
        tsMs: ms,
        ...vals,
      };
    });
  const bucketMs = chartData.length > 1 ? (chartData[1].tsMs - chartData[0].tsMs) || 60_000 : 60_000;

  // Color hex values for chart series
  const chartColors: Record<string, string> = {
    blue: "#3b82f6", orange: "#f97316", red: "#ef4444",
  };

  return (
    <AreaChart
      className="h-full"
      data={chartData}
      index="timestamp"
      categories={labels}
      colors={labels.map(() => chartColors[color] || color)}
      valueFormatter={format}
      showLegend={labels.length > 1}
      showGridLines={false}
      showXAxis={true}
      showYAxis={true}
      autoMinValue
      allowDecimals
      curveType="monotone"
      showGradient={false}
      tickGap={30}
      yAxisWidth={60}
      onRangeSelect={onRangeSelect}
      bucketMs={bucketMs}
    />
  );
}
