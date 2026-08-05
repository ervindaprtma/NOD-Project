"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken, apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatPercent, formatNumber, getDefaultTimeRange, TAB_TRIGGER_CLASS, formatBucketLabelWIB } from "@/lib/constants";
import type { ResourceData, HAStatusData, InterfaceStatsData, InterfaceStatsItem, DeviceAvailabilityData, DeviceAvailabilityItem } from "@/types";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";
import { AreaChart } from "@/components/charts/AreaChart";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@radix-ui/react-tabs";

type SectionId = "deviceStatus" | "timeline";

const SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"];

const SITE_BADGES: Record<string, string> = {
  "Site_FGT-DC": "DC",
  "Site_FGT-DRC": "DRC",
  "Site_FGT_Office": "Office",
};

// ── Tab index ─────────────────────────────────────────────────────
type TabIndex = 0 | 1 | 2;

const TAB_VALUES = ["resources", "bandwidth", "availability"] as const;

// Availability is an SLA-period concept, so it owns its own window rather than
// the page's 15m–24h presets.
const AVAILABILITY_WINDOWS = [
  { id: "24h", label: "24h", seconds: 86_400 },
  { id: "7d", label: "7d", seconds: 604_800 },
  { id: "30d", label: "30d", seconds: 2_592_000 },
  { id: "90d", label: "90d", seconds: 7_776_000 },
  { id: "365d", label: "365d", seconds: 31_536_000 },
] as const;

// Status chips are icon + text, never colour alone.
const DEVICE_STATUS_STYLE: Record<string, { label: string; icon: string; cls: string }> = {
  up: { label: "Up", icon: "●", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" },
  rebooted: { label: "Rebooted", icon: "⟳", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400" },
  not_reporting: { label: "Not reporting", icon: "◌", cls: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400" },
  collector_gap: { label: "Collector gap", icon: "▨", cls: "bg-muted text-muted-foreground" },
};

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatWIB(ms: number | null): string {
  if (!ms) return "—";
  return new Date(ms).toLocaleString("en-GB", {
    timeZone: "Asia/Jakarta", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

export default function ResourcesPage() {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [activePresetSeconds, setActivePresetSeconds] = useState(TIME_PRESETS[0].seconds);
  const [refreshInterval, setRefreshInterval] = useState(DEFAULT_REFRESH_MS);
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null);
  const [siteName, setSiteName] = useState("Site_FGT-DC");
  const [expanded, setExpanded] = useState<SectionId | null>(null);
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customRangeLabel, setCustomRangeLabel] = useState<string | null>(null);
  const [tabIndex, setTabIndex] = useState<TabIndex>(0);
  // Pre-select the site AND tab when navigated from Overview with ?site=…&tab=… (e.g. Site
  // Availability → tab=availability, Interface/WAN Bandwidth → tab=bandwidth). Read on the
  // client after mount; a useState initializer reading the URL would hydration-mismatch SSR.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const s = p.get("site");
    if (s && SITES.includes(s)) setSiteName(s);
    const ti = TAB_VALUES.indexOf(p.get("tab") as typeof TAB_VALUES[number]);
    if (ti >= 0) setTabIndex(ti as TabIndex);
  }, []);
  const [availWindow, setAvailWindow] = useState<string>("24h");
  // Drag-to-zoom on the availability charts narrows the query to a sub-range.
  const [availZoom, setAvailZoom] = useState<{ gteMs: number; lteMs: number } | null>(null);
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
    ? `/api/v1/resources?gte_ms=${currentGteMs}&lte_ms=${currentLteMs}&site_name=${siteName}`
    : null;

  const { data, error, isLoading } = useSWR<{ data: ResourceData; meta: { query_took_ms: number } }>(
    swrKey,
    swrFetcher,
    { refreshInterval: 0 }
  );

  const resources = data?.data;
  const queryTook = data?.meta?.query_took_ms;
  const devices = resources?.current || [];

  // HA Status (only for Site_FGT-DC)
  const haSwrKey = token && siteName === "Site_FGT-DC"
    ? `/api/v1/ha/status?site_name=${siteName}`
    : null;

  const { data: haData, error: haError, isLoading: haLoading } = useSWR<{ data: HAStatusData; meta: { query_took_ms: number } }>(
    haSwrKey,
    swrFetcher,
    { refreshInterval: refreshInterval > 0 ? refreshInterval : 0 }
  );

  const haStatus = haData?.data;

  // Interface Stats (Tab 2 — backend returns only hardcoded WAN/MPLS)
  const ifStatsSwrKey = token
    ? `/api/v1/interface-stats?site_name=${siteName}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}`
    : null;

  const { data: ifStatsData, error: ifStatsError, isLoading: ifStatsLoading } = useSWR<{ data: InterfaceStatsData; meta: { query_took_ms: number } }>(
    ifStatsSwrKey,
    swrFetcher,
    { refreshInterval: 0 }
  );

  const ifStats = ifStatsData?.data?.interfaces ?? [];

  // Device Availability (Tab 3) — its own window, independent of the page presets.
  const availSwrKey = token && tabIndex === 2
    ? availZoom
      ? `/api/v1/device-uptime?site_name=${siteName}&gte_ms=${availZoom.gteMs}&lte_ms=${availZoom.lteMs}`
      : `/api/v1/device-uptime?site_name=${siteName}&window=${availWindow}`
    : null;

  // Availability windows span up to 365d over telegraf-index*, which can take far
  // longer than the 30s default — the backend itself waits ~115s. Give the client
  // a longer leash so a slow-but-successful query isn't aborted into a false error.
  const { data: availData, error: availError, isLoading: availLoading, isValidating: availValidating, mutate: mutateAvail } =
    useSWR<{ data: DeviceAvailabilityData; meta: { query_took_ms: number; degraded?: boolean } }>(
      availSwrKey,
      (url: string) => apiFetch<{ data: DeviceAvailabilityData; meta: { query_took_ms: number; degraded?: boolean } }>(url, { timeoutMs: 120_000 }),
      { refreshInterval: 0 }
    );

  const availability = availData?.data;
  const availDevices: DeviceAvailabilityItem[] = availability?.devices ?? [];
  const availSummary = availability?.summary;

  // Flatten per-device reboot events + site collector gaps into one time-sorted history so
  // the operator can see WHAT happened WHEN — not just a count. `note` on a reboot means the
  // 32-bit uptime counter wrapped (~497d), which is NOT an outage. Detail is a fixed
  // description of the condition — we don't show a "how long unreachable" duration because the
  // poll-gap estimate around a reset isn't a reliable measure of real downtime.
  const eventHistory: { time: number; device: string; vendor: string; kind: "Reboot" | "Counter wrap" | "Collector gap"; detail: string }[] = [
    ...availDevices.flatMap((d) =>
      (d.reboots ?? []).map((r) => ({
        time: r.at_ms,
        device: d.hostname || d.device_key,
        vendor: d.vendor,
        kind: (r.note ? "Counter wrap" : "Reboot") as "Reboot" | "Counter wrap",
        detail: r.note
          ? "Uptime counter wrapped — not an outage."
          : "Device reboot and collectors cannot read the data from devices.",
      }))
    ),
    ...(availSummary?.collector_gaps ?? []).map((g) => ({
      time: g.end_ms,
      device: "— site-wide —",
      vendor: "",
      kind: "Collector gap" as const,
      detail: "Collectors timeout and data not collected.",
    })),
  ].sort((a, b) => b.time - a.time);

  const deviceIDs = [...new Set([
    ...(resources?.timeline?.cpu || []).map((d) => d.device),
    ...(resources?.timeline?.memory || []).map((d) => d.device),
  ])];

  const filteredDevices = selectedDevice
    ? devices.filter((d) => d.device === selectedDevice)
    : devices;
  const filteredDeviceIDs = selectedDevice
    ? [selectedDevice]
    : deviceIDs;

  function handlePreset(seconds: number, label: string) {
    const now = Date.now();
    setGteMs(now - seconds * 1000);
    setLteMs(now);
    setActivePresetSeconds(seconds);
    setSelectedPreset(label);
    setCustomRangeLabel(null);
    setShowCustomPicker(false);
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

  // Drag-to-zoom on any chart narrows the page's time range (same as the Traffic pages).
  // preZoomRef remembers the prior view so "Reset zoom" restores it.
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

  // ── Expanded view (full-screen section) ──────────────────────────
  if (expanded) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setExpanded(null)}
            className="px-3 py-1.5 text-xs border border-border/60 dark:border-border/40 rounded-md hover:bg-muted shadow-sm transition-all hover:shadow-md"
          >
            ← Back to Resources
          </button>
          <h1 className="text-xl font-bold tracking-tight">
            {expanded === "deviceStatus" ? "Device Status — Full View" : "Resource Timeline — Full View"}
          </h1>
        </div>
        {expanded === "deviceStatus" ? (
          <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-6 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {isLoading
                ? [1, 2].map((i) => (
                    <div key={i} className="bg-muted rounded-lg p-5 animate-pulse h-40" />
                  ))
                : filteredDevices.map((dev) => (
                    <div key={dev.hostname || dev.device} className="border rounded-lg p-5 space-y-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <h3 className="font-semibold text-lg">{dev.hostname || dev.device}</h3>
                          {dev.device && <p className="text-xs text-muted-foreground font-mono">{dev.device}</p>}
                        </div>
                        <div className={cn(
                          "flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium",
                          dev.sync_status === "In Sync" || dev.sync_status === "1" || String(dev.sync_status) === "1"
                            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                            : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                        )}>
                          <span className={cn("w-2 h-2 rounded-full",
                            (dev.sync_status === "In Sync" || dev.sync_status === "1") ? "bg-emerald-500" : "bg-red-500"
                          )} />
                          {dev.sync_status === "In Sync" || dev.sync_status === "1" || String(dev.sync_status) === "1"
                            ? "HA In Sync" : "HA Out of Sync"}
                        </div>
                      </div>
                      {dev.serial_number && (
                        <p className="text-xs text-muted-foreground font-mono">S/N: {dev.serial_number}</p>
                      )}
                      <div className="grid grid-cols-3 gap-4">
                        <MetricMini label="CPU" value={dev.cpu_usage != null ? formatPercent(dev.cpu_usage) : "—"} color="blue" />
                        <MetricMini label="Memory" value={dev.mem_usage != null ? formatPercent(dev.mem_usage) : "—"} color="amber" />
                        <MetricMini label="Sessions" value={dev.session_count != null ? formatNumber(dev.session_count) : "—"} color="purple" />
                      </div>
                    </div>
                  ))}
            </div>
          </div>
        ) : (
          <div className="space-y-6">
            {filteredDeviceIDs.map((device) => (
              <div key={device} className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-6 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
                <h3 className="font-semibold mb-4">{device}</h3>
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                  {isLoading ? (
                    [1,2,3].map(i => <div key={i} className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-3 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 animate-pulse"><div className="h-3 bg-muted rounded w-20 mb-2" /><div className="h-28 bg-muted rounded" /></div>)
                  ) : (
                    <>
                      <ResourceAreaCard
                        title="CPU Usage (%)"
                        data={(resources?.timeline?.cpu || []).filter(d => d.device === device)}
                        color="blue" valueFormatter={formatPercent}
                        onRangeSelect={applyBrushRange}
                      />
                      <ResourceAreaCard
                        title="Memory Usage (%)"
                        data={(resources?.timeline?.memory || []).filter(d => d.device === device)}
                        color="amber" valueFormatter={formatPercent}
                        onRangeSelect={applyBrushRange}
                      />
                      <ResourceAreaCard
                        title="Active Sessions"
                        data={(resources?.timeline?.sessions || []).filter(d => d.device === device)}
                        color="purple" valueFormatter={formatNumber}
                        onRangeSelect={applyBrushRange}
                      />
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
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

  // ── Main (tabbed) view ───────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">FortiGate Resources</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={siteName}
            onChange={(e) => setSiteName(e.target.value)}
            className="px-3 py-1.5 rounded-md border border-border/60 dark:border-border/40 bg-background text-sm shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-shadow"
          >
            {SITES.map((s) => (
              <option key={s} value={s}>{s.replace("_", " ")}</option>
            ))}
          </select>
          <span className={cn(
            "px-2 py-0.5 rounded-md text-[11px] font-semibold uppercase tracking-wide shadow-sm",
            siteName.includes("DC") && !siteName.includes("DRC") ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" :
            siteName.includes("DRC") ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" :
            "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
          )}>
            {SITE_BADGES[siteName] || siteName}
          </span>
          {/* Availability owns its own SLA-period window selector, so the page
              time-range presets don't apply there — hide them on that tab. */}
          {TAB_VALUES[tabIndex] !== "availability" && (
            <div className="flex gap-1 bg-muted rounded-md p-1">
              {TIME_PRESETS.map((p) => (
                <button
                  key={p.label}
                  onClick={() => handlePreset(p.seconds, p.label)}
                  className={cn(
                    "px-2.5 py-1 text-xs rounded-sm transition-colors",
                    selectedPreset === p.label
                      ? "bg-background text-foreground shadow ring-1 ring-black/5 dark:ring-white/20"
                      : "text-muted-foreground hover:text-foreground hover:bg-background/50 dark:hover:bg-background/20"
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
                    ? "bg-background text-foreground shadow ring-1 ring-black/5 dark:ring-white/20"
                    : "text-muted-foreground hover:text-foreground hover:bg-background/50 dark:hover:bg-background/20"
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
          )}
          {isZoomed && (
            <button
              onClick={resetZoom}
              className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted/50 transition-colors"
              title="Restore the view before drag-zoom"
            >
              ⟲ Reset zoom
            </button>
          )}
          <select
            value={refreshInterval}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="h-7 px-2 text-xs border border-border/60 dark:border-border/40 rounded-md bg-card text-muted-foreground cursor-pointer shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 focus:outline-none focus:ring-1 focus:ring-primary/20 hover:border-border dark:hover:border-border/60 transition-colors"
          >
            {REFRESH_INTERVALS.map((ri) => (
              <option key={ri.value} value={ri.value}>
                {ri.label === "Off" ? "⏸ Off" : `↻ ${ri.label}`}
              </option>
            ))}
          </select>
          {queryTook != null && (
            <span className="text-xs text-muted-foreground">{queryTook}ms</span>
          )}
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
          Failed to load resource data.{" "}
          <button onClick={() => window.location.reload()} className="underline hover:no-underline transition-all dark:text-primary-foreground/80">Retry</button>
        </div>
      )}

      {/* ── Tab Group ────────────────────────────────────────────── */}
      <Tabs
        value={TAB_VALUES[tabIndex]}
        onValueChange={(val) => setTabIndex(Math.max(0, TAB_VALUES.indexOf(val as typeof TAB_VALUES[number])) as TabIndex)}
      >
        <TabsList className="mb-4 p-1 gap-1 bg-muted/40 dark:bg-muted/30 rounded-lg inline-flex">
          <TabsTrigger value="resources" className={TAB_TRIGGER_CLASS}>Resource Usage</TabsTrigger>
          <TabsTrigger value="bandwidth" className={TAB_TRIGGER_CLASS}>Interface Bandwidth</TabsTrigger>
          <TabsTrigger value="availability" className={TAB_TRIGGER_CLASS}>Availability</TabsTrigger>
        </TabsList>

          {/* ════════════════════════════════════════════════════════
              TAB 1 — Resource Usage
              ════════════════════════════════════════════════════════ */}
          <TabsContent value="resources">
            <div className="space-y-6">
              {/* HA Status Panel (Site_FGT-DC only) */}
              {siteName === "Site_FGT-DC" && (
                <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-4 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
                  <h2 className="text-sm font-semibold mb-3 tracking-tight">HA Cluster Status</h2>
                  {haLoading ? (
                    <div className="space-y-2">
                      <div className="h-6 bg-muted rounded w-48 animate-pulse" />
                      <div className="h-20 bg-muted rounded animate-pulse" />
                    </div>
                  ) : haError ? (
                    <div className="p-3 rounded bg-destructive/10 text-destructive text-sm">
                      Failed to load HA status. The cluster may be unreachable.
                    </div>
                  ) : haStatus ? (
                    <div className="space-y-3">
                      <div className="flex items-center gap-4 flex-wrap">
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">HA Mode:</span>
                          <span className="text-sm font-semibold">{haStatus.ha_mode}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">Members:</span>
                          <span className="text-sm font-semibold">{haStatus.members?.length || 0}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">Health:</span>
                          <span className={cn(
                            "px-2 py-0.5 rounded-full text-xs font-medium",
                            haStatus.overallHealth === "healthy"
                              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                              : haStatus.overallHealth === "degraded"
                              ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
                              : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                          )}>
                            <span className={cn(
                              "inline-block w-1.5 h-1.5 rounded-full mr-1",
                              haStatus.overallHealth === "healthy" ? "bg-emerald-500"
                              : haStatus.overallHealth === "degraded" ? "bg-amber-500"
                              : "bg-red-500"
                            )} />
                            {haStatus.overallHealth}
                          </span>
                        </div>
                      </div>
                      {haStatus.members && haStatus.members.length > 0 && (
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b text-muted-foreground text-left">
                              <th className="py-2">Role</th>
                              <th className="py-2">Sync Status</th>
                              <th className="py-2">Priority</th>
                            </tr>
                          </thead>
                          <tbody>
                            {haStatus.members.map((member, i) => (
                              <tr key={i} className="border-b last:border-0">
                                <td className="py-2">
                                  <span className={cn(
                                    "px-2 py-0.5 rounded text-[11px] font-medium",
                                    member.role === "primary" || member.role === "master"
                                      ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                                      : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                                  )}>
                                    {member.role}
                                  </span>
                                </td>
                                <td className="py-2">
                                  <span className={cn(
                                    "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
                                    member.syncStatus === "In Sync" || member.syncStatus === "1" || String(member.syncStatus) === "1"
                                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                                      : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                                  )}>
                                    <span className={cn("w-1.5 h-1.5 rounded-full",
                                      (member.syncStatus === "In Sync" || member.syncStatus === "1" || String(member.syncStatus) === "1")
                                        ? "bg-emerald-500" : "bg-red-500"
                                    )} />
                                    {member.syncStatus}
                                  </span>
                                </td>
                                <td className="py-2 font-mono text-xs">{member.priority}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No HA status data available.</p>
                  )}
                </div>
              )}

              {/* RES-01: Device Selector */}
              <div className="flex items-center gap-3">
                <span className="text-sm text-muted-foreground">Device:</span>
                <select
                  value={selectedDevice || ""}
                  onChange={(e) => setSelectedDevice(e.target.value || null)}
                  className="px-3 py-1.5 rounded-md border border-border/60 dark:border-border/40 bg-background text-sm shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30 transition-shadow"
                >
                  <option value="">All Devices ({devices.length})</option>
                  {devices.map((d) => (
                    <option key={d.device + (d.hostname || "")} value={d.hostname || d.device}>
                      {d.hostname || d.device}{d.device ? ` (${d.device})` : ""}
                    </option>
                  ))}
                </select>
              </div>

              {/* Device Status Cards */}
              <div className="group">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-lg font-semibold tracking-tight">Device Status</h2>
                  <button
                    onClick={() => setExpanded("deviceStatus")}
                    className="text-[11px] text-muted-foreground hover:text-primary transition-all opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded hover:bg-muted hover:shadow-sm dark:hover:bg-white/5"
                  >
                    View Full ↗
                  </button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {isLoading
                    ? [1, 2].map((i) => (
                        <div key={i} className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-5 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 animate-pulse space-y-3">
                          <div className="h-5 bg-muted rounded w-24" />
                          <div className="h-4 bg-muted rounded w-32" />
                          <div className="grid grid-cols-2 gap-3">
                            <div className="h-16 bg-muted rounded" />
                            <div className="h-16 bg-muted rounded" />
                          </div>
                        </div>
                      ))
                    : filteredDevices.map((dev) => (
                        <div key={dev.hostname || dev.device} className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-5 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 space-y-3">
                          <div className="flex items-center justify-between">
                            <div>
                              <h3 className="font-semibold">{dev.hostname || dev.device}</h3>
                              {dev.device && (
                                <p className="text-xs text-muted-foreground font-mono">{dev.device}</p>
                              )}
                            </div>
                            <div className={cn(
                              "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
                              dev.sync_status === "standalone"
                                ? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                                : dev.sync_status === "In Sync" || dev.sync_status === "1" || String(dev.sync_status) === "1"
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                                : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                            )}>
                              <span className={cn(
                                "w-1.5 h-1.5 rounded-full",
                                dev.sync_status === "standalone" ? "bg-slate-500"
                                : (dev.sync_status === "In Sync" || dev.sync_status === "1") ? "bg-emerald-500" : "bg-red-500"
                              )} />
                              {dev.sync_status === "standalone" ? "Standalone"
                                : dev.sync_status === "In Sync" || dev.sync_status === "1" || String(dev.sync_status) === "1" ? "HA In Sync" : "HA Out of Sync"}
                            </div>
                          </div>
                          {dev.serial_number && (
                            <p className="text-[10px] text-muted-foreground font-mono">S/N: {dev.serial_number}</p>
                          )}
                          <div className="grid grid-cols-3 gap-3">
                            <MetricMini label="CPU" value={dev.cpu_usage != null ? formatPercent(dev.cpu_usage) : "—"} color="blue" />
                            <MetricMini label="Memory" value={dev.mem_usage != null ? formatPercent(dev.mem_usage) : "—"} color="amber" />
                            <MetricMini label="Sessions" value={dev.session_count != null ? formatNumber(dev.session_count) : "—"} color="purple" />
                          </div>
                          {dev.mem_capacity_kb ? (
                            <p className="text-[10px] text-muted-foreground">
                              RAM: {(dev.mem_capacity_kb / 1048576).toFixed(1)} GB
                            </p>
                          ) : null}
                        </div>
                      ))}
                </div>
              </div>

              {/* Timeline Charts */}
              <div className="group">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-lg font-semibold tracking-tight">Resource Timeline</h2>
                  <button
                    onClick={() => setExpanded("timeline")}
                    className="text-[11px] text-muted-foreground hover:text-primary transition-all opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded hover:bg-muted hover:shadow-sm dark:hover:bg-white/5"
                  >
                    View Full ↗
                  </button>
                </div>
                {filteredDeviceIDs.map((device) => (
                  <div key={device} className="space-y-4 mb-4">
                    <h3 className="text-md font-semibold text-muted-foreground">{device}</h3>
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                      {isLoading ? (
                        [1,2,3].map(i => <div key={i} className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-3 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 animate-pulse"><div className="h-3 bg-muted rounded w-20 mb-2" /><div className="h-28 bg-muted rounded" /></div>)
                      ) : (
                        <>
                          <ResourceAreaCard
                            title="CPU Usage (%)"
                            data={(resources?.timeline?.cpu || []).filter(d => d.device === device)}
                            color="blue" valueFormatter={formatPercent}
                            onRangeSelect={applyBrushRange}
                          />
                          <ResourceAreaCard
                            title="Memory Usage (%)"
                            data={(resources?.timeline?.memory || []).filter(d => d.device === device)}
                            color="amber" valueFormatter={formatPercent}
                            onRangeSelect={applyBrushRange}
                          />
                          <ResourceAreaCard
                            title="Active Sessions"
                            data={(resources?.timeline?.sessions || []).filter(d => d.device === device)}
                            color="purple" valueFormatter={formatNumber}
                            onRangeSelect={applyBrushRange}
                          />
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </TabsContent>
          {/* ════════════════════════════════════════════════════════
              TAB 2 — Interface Bandwidth
              ════════════════════════════════════════════════════════ */}
          <TabsContent value="bandwidth">
            <div className="space-y-4">
              {/* Section header */}
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold tracking-tight">
                  WAN / MPLS Interfaces — {siteName.replace(/_/g, " ")}
                </h2>
                {ifStats.length > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {ifStats.length} interface{ifStats.length !== 1 ? "s" : ""}
                  </span>
                )}
              </div>

              {/* Loading state */}
              {ifStatsLoading && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-5 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 animate-pulse space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="h-5 bg-muted rounded w-28" />
                        <div className="h-5 bg-muted rounded w-14" />
                      </div>
                      <div className="h-4 bg-muted rounded w-20" />
                      <div className="grid grid-cols-2 gap-3">
                        <div className="h-10 bg-muted rounded" />
                        <div className="h-10 bg-muted rounded" />
                      </div>
                      <div className="h-36 bg-muted rounded" />
                    </div>
                  ))}
                </div>
              )}

              {/* Error state */}
              {!ifStatsLoading && ifStatsError && (
                <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
                  Failed to load interface stats.{" "}
                  <button onClick={() => window.location.reload()} className="underline hover:no-underline transition-all dark:text-primary-foreground/80">Retry</button>
                </div>
              )}

              {/* Empty state */}
              {!ifStatsLoading && !ifStatsError && ifStats.length === 0 && (
                <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-8 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 text-center">
                  <p className="text-sm text-muted-foreground">
                    No WAN or MPLS interfaces found for this site.
                  </p>
                </div>
              )}

              {/* Interface cards grid */}
              {!ifStatsLoading && !ifStatsError && ifStats.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {ifStats.map((iface, i) => (
                    <InterfaceBandwidthCard key={i} iface={iface} onRangeSelect={applyBrushRange} />
                  ))}
                </div>
              )}
            </div>
          </TabsContent>

          {/* ── Tab 3: Device Availability ─────────────────────────── */}
          <TabsContent value="availability">
            <div className="space-y-4">
              {/* Window selector — owns its own range, not the page presets */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground mr-1">Window</span>
                {AVAILABILITY_WINDOWS.map((w) => (
                  <button
                    key={w.id}
                    onClick={() => setAvailWindow(w.id)}
                    className={cn(
                      "px-2.5 py-1 text-xs rounded-md border transition-colors",
                      availWindow === w.id
                        ? "bg-primary/10 text-primary border-primary/30 font-medium"
                        : "bg-card text-muted-foreground border-border/60 hover:bg-muted/50"
                    )}
                  >
                    {w.label}
                  </button>
                ))}
                {availSummary && !availSummary.history_sufficient && !availZoom && (
                  <span className="text-xs text-amber-600 dark:text-amber-400 ml-1">
                    ⚠ history starts {formatWIB(availSummary.history_start_ms)} — longer windows are
                    clamped to available data
                  </span>
                )}
              </div>

              {/* Zoomed: every figure below is for the selection, not the window —
                  say so rather than let a dragged range masquerade as the SLA. */}
              {availZoom && (
                <div className="flex items-center gap-3 flex-wrap rounded-lg border border-primary/30 bg-primary/5 px-3 py-2">
                  <span className="text-xs text-foreground">
                    Showing selection{" "}
                    <span className="font-medium">
                      {formatWIB(availZoom.gteMs)} → {formatWIB(availZoom.lteMs)}
                    </span>{" "}
                    — figures below are for this range, not the {availWindow} window.
                  </span>
                  <button
                    onClick={() => setAvailZoom(null)}
                    className="text-xs px-2 py-1 rounded-md border border-border/60 bg-card hover:bg-muted/50"
                  >
                    ⟲ Reset zoom
                  </button>
                </div>
              )}

              {availLoading && (
                <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-8 text-center">
                  <p className="text-sm text-muted-foreground">Loading device availability…</p>
                </div>
              )}

              {availError && (
                <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-8 text-center space-y-3">
                  <p className="text-sm text-destructive">
                    Couldn&apos;t load device availability
                    {(availError as { message?: string })?.message ? ` — ${(availError as { message?: string }).message}` : "."}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Wider windows (30d–365d) query a lot of data and can time out. Try again, or pick a shorter window.
                  </p>
                  <button
                    onClick={() => mutateAvail()}
                    disabled={availValidating}
                    className="inline-flex items-center gap-1 rounded-md border border-border/60 px-3 py-1.5 text-xs font-medium hover:bg-muted/50 transition-colors disabled:opacity-50"
                  >
                    {availValidating ? "Retrying…" : "↻ Retry"}
                  </button>
                </div>
              )}

              {/* Backend answered but the data source was slow/unavailable (safe_search
                  returns an empty skeleton on timeout, flagged via meta.degraded). Say so
                  and offer a retry instead of silently showing "no devices". */}
              {!availLoading && !availError && availData?.meta?.degraded && (
                <div className="rounded-lg border border-amber-300/60 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-900/20 p-4 flex items-center justify-between gap-3 flex-wrap">
                  <p className="text-sm text-amber-800 dark:text-amber-300">
                    ⚠ The data source was slow or unavailable for this window — results may be
                    incomplete.
                  </p>
                  <button
                    onClick={() => mutateAvail()}
                    disabled={availValidating}
                    className="inline-flex items-center gap-1 rounded-md border border-amber-400/60 px-3 py-1.5 text-xs font-medium text-amber-800 dark:text-amber-300 hover:bg-amber-100/60 dark:hover:bg-amber-900/30 transition-colors disabled:opacity-50"
                  >
                    {availValidating ? "Retrying…" : "↻ Retry"}
                  </button>
                </div>
              )}

              {!availLoading && !availError && availSummary && (
                <>
                  {/* Summary — counts, not a fleet average (availability is per device) */}
                  <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-4 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
                      <span className="font-medium">
                        {availSummary.devices_reporting} / {availSummary.devices_total} reporting
                      </span>
                      <span className="text-muted-foreground">
                        {availSummary.reboots_total} reboot{availSummary.reboots_total === 1 ? "" : "s"}
                      </span>
                      <span className="text-muted-foreground">
                        {availSummary.collector_gaps.length} collector gap
                        {availSummary.collector_gaps.length === 1 ? "" : "s"}
                        {availSummary.collector_gap_seconds > 0 &&
                          ` (${formatDuration(availSummary.collector_gap_seconds)})`}
                      </span>
                      {availSummary.lowest_uptime_device && (
                        <span className="text-muted-foreground" title="Most recently booted device — smallest uptime, not lowest availability">
                          Lowest uptime{" "}
                          <span className="text-foreground font-medium">
                            {availSummary.lowest_uptime_device.hostname}{" "}
                            {availSummary.lowest_uptime_device.uptime_human_short}
                          </span>
                        </span>
                      )}
                      {availSummary.devices_partial_history > 0 && (
                        <span className="text-muted-foreground">
                          {availSummary.devices_partial_history} partial history
                        </span>
                      )}
                    </div>
                    {availSummary.collector_gaps.length > 0 && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        A collector gap means no data reached us from this site — it does not by
                        itself mean the devices were down. Gaps are excluded from availability.
                      </p>
                    )}
                  </div>

                  {/* Device table */}
                  {availDevices.length === 0 ? (
                    <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-8 text-center">
                      <p className="text-sm text-muted-foreground">
                        No devices reporting uptime for this site.
                      </p>
                    </div>
                  ) : (
                    <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-border/60 dark:border-border/40 text-xs text-muted-foreground">
                            <th className="text-left font-medium px-4 py-2.5">Device</th>
                            <th className="text-left font-medium px-4 py-2.5">Vendor</th>
                            <th className="text-left font-medium px-4 py-2.5">Status</th>
                            <th className="text-right font-medium px-4 py-2.5">Avail %</th>
                            <th className="text-right font-medium px-4 py-2.5">UP for</th>
                            <th className="text-left font-medium px-4 py-2.5">Booted (WIB)</th>
                            <th className="text-right font-medium px-4 py-2.5">Reboots</th>
                          </tr>
                        </thead>
                        <tbody>
                          {availDevices.map((d) => {
                            const st = DEVICE_STATUS_STYLE[d.status] ?? DEVICE_STATUS_STYLE.up;
                            return (
                              <tr
                                key={d.device_key}
                                className="border-b border-border/40 dark:border-border/20 last:border-0 hover:bg-muted/30"
                              >
                                <td className="px-4 py-2.5">
                                  <div className="font-medium">{d.hostname}</div>
                                  <div className="text-xs text-muted-foreground">{d.device_key}</div>
                                </td>
                                <td className="px-4 py-2.5 text-muted-foreground">{d.vendor}</td>
                                <td className="px-4 py-2.5">
                                  <span
                                    className={cn(
                                      "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
                                      st.cls
                                    )}
                                  >
                                    <span aria-hidden="true">{st.icon}</span>
                                    {st.label}
                                  </span>
                                  {d.partial_history && (
                                    <span
                                      className="ml-1 text-xs text-muted-foreground"
                                      title="Onboarded partway through the window — availability is measured from its first sample"
                                    >
                                      ◐
                                    </span>
                                  )}
                                  {d.wrap_risk && (
                                    <span
                                      className="ml-1 text-xs text-amber-600 dark:text-amber-400"
                                      title="Uptime counter is approaching its 32-bit wrap (~497 days)"
                                    >
                                      ⚠
                                    </span>
                                  )}
                                </td>
                                <td className="px-4 py-2.5 text-right tabular-nums">
                                  {d.availability_pct === null ? (
                                    <span
                                      className="text-muted-foreground"
                                      title="Not enough history to judge — no estimate is shown rather than a misleading one"
                                    >
                                      —
                                    </span>
                                  ) : (
                                    `${d.availability_pct.toFixed(2)}%`
                                  )}
                                </td>
                                <td className="px-4 py-2.5 text-right tabular-nums whitespace-nowrap">
                                  {d.uptime_human_long}
                                </td>
                                <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap">
                                  {formatWIB(d.boot_time_ms)}
                                </td>
                                <td className="px-4 py-2.5 text-right tabular-nums">
                                  {d.reboot_count}
                                  {d.total_downtime_seconds > 0 && (
                                    <div className="text-xs text-muted-foreground">
                                      {formatDuration(d.total_downtime_seconds)} down
                                    </div>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* Reboot & event history — what happened, when (newest first) */}
                  <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 overflow-x-auto">
                    <div className="px-4 py-2.5 border-b border-border/60 dark:border-border/40 flex items-center justify-between">
                      <h3 className="text-sm font-semibold">Reboot &amp; Event History</h3>
                      <span className="text-xs text-muted-foreground">{eventHistory.length} event{eventHistory.length === 1 ? "" : "s"} in this window</span>
                    </div>
                    {eventHistory.length === 0 ? (
                      <p className="px-4 py-6 text-sm text-muted-foreground text-center">
                        No reboots or collector gaps recorded in this window — every device stayed up.
                      </p>
                    ) : (
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-border/60 dark:border-border/40 text-xs text-muted-foreground">
                            <th className="text-left font-medium px-4 py-2.5">When (WIB)</th>
                            <th className="text-left font-medium px-4 py-2.5">Device</th>
                            <th className="text-left font-medium px-4 py-2.5">Vendor</th>
                            <th className="text-left font-medium px-4 py-2.5">Event</th>
                            <th className="text-left font-medium px-4 py-2.5">Detail</th>
                          </tr>
                        </thead>
                        <tbody>
                          {eventHistory.map((e, i) => {
                            const kindCls =
                              e.kind === "Reboot" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                                : e.kind === "Collector gap" ? "bg-muted text-muted-foreground"
                                  : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
                            const kindIcon = e.kind === "Reboot" ? "⟳" : e.kind === "Collector gap" ? "▨" : "↻";
                            return (
                              <tr key={`${e.time}-${e.device}-${i}`} className="border-b border-border/40 dark:border-border/20 last:border-0 hover:bg-muted/30">
                                <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap tabular-nums">{formatWIB(e.time)}</td>
                                <td className="px-4 py-2.5 font-medium">{e.device}</td>
                                <td className="px-4 py-2.5 text-muted-foreground">{e.vendor || "—"}</td>
                                <td className="px-4 py-2.5">
                                  <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium", kindCls)}>
                                    <span aria-hidden="true">{kindIcon}</span>{e.kind}
                                  </span>
                                </td>
                                <td className="px-4 py-2.5 text-muted-foreground">{e.detail}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                  </div>

                  {/* Per-device history — small multiples, one card each */}
                  {availDevices.length > 0 && (
                    <div className="space-y-4">
                      {availDevices.map((d) => (
                        <DeviceAvailabilityCard
                          key={d.device_key}
                          device={d}
                          gaps={availSummary.collector_gaps}
                          onRangeSelect={(g, l) => setAvailZoom({ gteMs: g, lteMs: l })}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </TabsContent>
      </Tabs>

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

// ── Byte-volume formatter (auto MB / GB / TB) ────────────────────
function formatBytes(bytes: number): string {
  if (!bytes || bytes < 0) return "0 MB";
  const tb = bytes / 1e12;
  if (tb >= 1) return `${tb.toFixed(2)} TB`;
  const gb = bytes / 1e9;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  return `${(bytes / 1e6).toFixed(1)} MB`;
}

function formatMbps(v: number | null): string {
  if (v == null) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)} Gbps` : `${v.toFixed(1)} Mbps`;
}

// ── Interface Bandwidth Card ─────────────────────────────────────
function InterfaceBandwidthCard({ iface, onRangeSelect }: { iface: InterfaceStatsItem; onRangeSelect?: (gteMs: number, lteMs: number) => void }) {
  const isUp = iface.oper_status === 1;

  const chartData = (iface.timeline || []).map((pt, idx) => {
    const ms = pt.timestamp ? new Date(pt.timestamp).getTime() : 0;
    return {
      timestamp: ms ? formatBucketLabelWIB(ms, idx > 0 ? new Date(iface.timeline![idx - 1].timestamp).getTime() : null) : pt.timestamp,
      In: pt.in_mbps ?? 0,
      Out: pt.out_mbps ?? 0,
      tsMs: ms,
    };
  });

  const hasTimeline = chartData.length > 1;
  const bucketMs = chartData.length > 1 ? (chartData[1].tsMs - chartData[0].tsMs) || 60_000 : 60_000;

  return (
    <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-semibold font-mono truncate" title={iface.label}>
            {iface.label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {hasTimeline && (
            <span
              className="text-[10px] text-muted-foreground"
              title="Avg/Peak/Last are per-bucket rates at this interval — 'Last' is the final bucket's average, not a live reading."
            >
              {bucketMs >= 3_600_000
                ? `${Math.round(bucketMs / 3_600_000)}h buckets`
                : `${Math.round(bucketMs / 60_000)}m buckets`}
            </span>
          )}
          {iface.speed_mbps != null && (
            <span className="inline-flex items-center rounded-md bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 px-2 py-0.5 text-[11px] font-medium">
              {iface.speed_mbps >= 1000
                ? `${(iface.speed_mbps / 1000).toFixed(1)} Gbps`
                : `${iface.speed_mbps.toLocaleString()} Mbps`}
            </span>
          )}
          <span className={cn("inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium", isUp ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300" : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300")}>
            <span
              className={cn(
                "inline-block w-1.5 h-1.5 rounded-full mr-1",
                isUp ? "bg-emerald-500" : "bg-red-500"
              )}
            />
            {isUp ? "UP" : "DOWN"}
          </span>
        </div>
      </div>

      {/* Avg / Peak / Last — blue row inbound, orange row outbound, matching the chart series */}
      <div className="space-y-2">
        <div className="grid grid-cols-3 gap-2">
          {(["Avg", "Peak", "Last"] as const).map((h) => (
            <p key={h} className="text-[10px] text-muted-foreground uppercase tracking-wider text-center">
              {h}
            </p>
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
          <p className="text-sm font-semibold text-blue-600 dark:text-blue-400">
            {formatBytes(iface.total_in_bytes)}
          </p>
        </div>
        <div className="bg-orange-50/60 dark:bg-orange-950/10 rounded-lg p-2 text-center">
          <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Volume Out</p>
          <p className="text-sm font-semibold text-orange-600 dark:text-orange-400">
            {formatBytes(iface.total_out_bytes)}
          </p>
        </div>
      </div>

      {hasTimeline ? (
        <div className="h-40 [&_text]:fill-gray-500 dark:[&_text]:fill-gray-400">
          <AreaChart
            className="h-full"
            data={chartData}
            index="timestamp"
            categories={["In", "Out"]}
            colors={["blue", "orange"]}
            valueFormatter={(v: number) =>
              v >= 1000 ? `${(v / 1000).toFixed(1)} Gbps` : `${v.toFixed(1)} Mbps`
            }
            showLegend={true}
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
        </div>
      ) : (
        <div className="bg-muted/30 rounded-lg h-40 flex items-center justify-center">
          <p className="text-xs text-muted-foreground">No timeline data available</p>
        </div>
      )}
    </div>
  );
}

// ── MetricMini ────────────────────────────────────────────────────
function MetricMini({ label, value, color }: { label: string; value: string; color: string }) {
  const colors: Record<string, string> = {
    blue: "bg-blue-50 border-blue-200 dark:bg-blue-950/30 dark:border-blue-800",
    amber: "bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800",
    purple: "bg-purple-50 border-purple-200 dark:bg-purple-950/30 dark:border-purple-800",
  };
  return (
    <div className={cn("rounded-lg border p-2 text-center", colors[color] || colors.blue)}>
      <p className="text-[10px] text-muted-foreground uppercase tracking-wider">{label}</p>
      <p className="text-lg font-bold">{value}</p>
    </div>
  );
}

// ── ResourceAreaCard — Tremor AreaChart with dark mode support ────
const AREA_COLORS: Record<string, string> = {
  blue: "#3b82f6",
  amber: "#f59e0b",
  purple: "#8b5cf6",
};

function ResourceAreaCard({
  title, data, color, valueFormatter, onRangeSelect,
}: {
  title: string; data: { timestamp: number; value: number }[];
  color: string; valueFormatter: (v: number) => string;
  onRangeSelect?: (gteMs: number, lteMs: number) => void;
}) {
  if (!data || data.length === 0) {
    return (
      <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-3 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
        <p className="text-xs font-medium mb-2">{title}</p>
        <p className="text-xs text-muted-foreground py-8 text-center">No data</p>
      </div>
    );
  }

  const chartData = data.map((d, idx) => {
    const ms = d.timestamp ? new Date(d.timestamp).getTime() : 0;
    return {
      timestamp: ms ? formatBucketLabelWIB(ms, idx > 0 ? new Date(data[idx - 1].timestamp).getTime() : null) : d.timestamp,
      value: d.value,
      tsMs: ms,
    };
  });
  // Bucket width from the data spacing, for the drag-zoom range end.
  const bucketMs = chartData.length > 1 ? (chartData[1].tsMs - chartData[0].tsMs) || 60_000 : 60_000;

  return (
    <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-3 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
      <p className="text-xs font-medium mb-1">{title}</p>
      <div className="h-32 [&_text]:fill-gray-500 dark:[&_text]:fill-gray-400">
        <AreaChart
          className="h-full"
          data={chartData}
          index="timestamp"
          categories={["value"]}
          colors={[AREA_COLORS[color] || color]}
          valueFormatter={valueFormatter}
          showLegend={false}
          showGridLines={false}
          showXAxis={true}
          showYAxis={true}
          autoMinValue
          allowDecimals
          curveType="monotone"
          showGradient={false}
          tickGap={30}
          yAxisWidth={50}
          onRangeSelect={onRangeSelect}
          bucketMs={bucketMs}
        />
      </div>
    </div>
  );
}

// ── Device Availability card ──────────────────────────────────────
// Small multiples: one card per device rather than 11 series on one axis.
// Two stacked panels share the x-axis — availability and uptime are different
// scales, and a second y-axis on one plot is the classic way to mislead.
function DeviceAvailabilityCard({
  device,
  gaps,
  onRangeSelect,
}: {
  device: DeviceAvailabilityItem;
  gaps: { start_ms: number; end_ms: number; duration_seconds: number }[];
  onRangeSelect?: (gteMs: number, lteMs: number) => void;
}) {
  const st = DEVICE_STATUS_STYLE[device.status] ?? DEVICE_STATUS_STYLE.up;

  const chartData = device.series.map((p, i) => ({
    // Buckets here are 15m–1d, so seconds are noise; the date matters across days.
    timestamp: formatBucketLabelWIB(p.ts_ms, i > 0 ? device.series[i - 1].ts_ms : null, false, true),
    tsMs: p.ts_ms,
    // Kept only as the "has data in this bucket" gate for hasSeries; counter-based
    // availability is 100/0/null, so it's no longer charted as its own panel.
    Availability: p.availability_pct,
    // Uptime in days; null in an empty bucket so the line breaks rather than
    // drawing a straight segment across a gap it has no data for.
    Uptime: p.uptime_seconds == null ? null : p.uptime_seconds / 86400,
  }));

  const bucketMs =
    chartData.length > 1 ? (chartData[1].tsMs - chartData[0].tsMs) || 60_000 : 60_000;

  // Map gap ranges onto bucket labels so Recharts can place them on a category axis.
  const labelAt = (ms: number) =>
    chartData.find((d) => d.tsMs >= ms)?.timestamp ?? chartData[chartData.length - 1]?.timestamp;
  const bands = gaps
    .filter((g) => g.end_ms >= (chartData[0]?.tsMs ?? 0))
    .map((g) => ({ x1: labelAt(g.start_ms), x2: labelAt(g.end_ms), label: "collector gap" }));

  // Real reboots only — a counter wrap carries a note and is not an outage.
  const markers = device.reboots
    .filter((r) => r.note == null)
    .map((r) => ({ x: labelAt(r.at_ms), label: `⟳ ${Math.round(r.downtime_seconds / 60)}m` }));

  const hasSeries = chartData.some((d) => d.Availability != null);

  return (
    <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-5 space-y-3">
      {/* Header doubles as the legend: identity, uptime duration, availability */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-baseline gap-2 min-w-0">
          <span className="text-sm font-semibold font-mono truncate" title={device.hostname}>
            {device.hostname}
          </span>
          <span className="text-[11px] text-muted-foreground truncate">
            {device.vendor} · {device.device_key}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[11px] text-muted-foreground">
            UP for <span className="text-foreground font-medium">{device.uptime_human_long}</span>
          </span>
          <span className="text-[11px] tabular-nums font-medium">
            {device.availability_pct === null ? "—" : `${device.availability_pct.toFixed(2)}%`}
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
              st.cls
            )}
          >
            <span aria-hidden="true">{st.icon}</span>
            {st.label}
          </span>
        </div>
      </div>

      {!hasSeries ? (
        <p className="text-xs text-muted-foreground py-6 text-center">
          No samples in this range.
        </p>
      ) : (
        <>
          {/* Uptime (days). A reset to zero is a reboot; collector gaps and reboot
              markers ride along on this panel. The former poll-success panel was
              removed — availability is now counter-based, so its series was a flat
              100% line; the headline % in the header carries it instead. */}
          <div>
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-1">
              Uptime (days)
            </p>
            <div className="h-[90px]">
              <AreaChart
                className="h-full"
                data={chartData}
                index="timestamp"
                categories={["Uptime"]}
                colors={["blue"]}
                valueFormatter={(v: number) => (v == null ? "—" : `${v.toFixed(1)}d`)}
                showLegend={false}
                showGridLines={false}
                showXAxis={true}
                showYAxis={true}
                autoMinValue
                curveType="monotone"
                showGradient={false}
                yAxisWidth={44}
                tickGap={40}
                onRangeSelect={onRangeSelect}
                bucketMs={bucketMs}
                bands={bands}
                markers={markers}
              />
            </div>
          </div>

          <p className="text-[10px] text-muted-foreground">
            {bucketMs >= 86_400_000
              ? `${Math.round(bucketMs / 86_400_000)}d buckets`
              : bucketMs >= 3_600_000
                ? `${Math.round(bucketMs / 3_600_000)}h buckets`
                : `${Math.round(bucketMs / 60_000)}m buckets`}
            {" · drag to zoom"}
            {device.partial_history && " · onboarded mid-window, measured from its first sample"}
          </p>
        </>
      )}
    </div>
  );
}
