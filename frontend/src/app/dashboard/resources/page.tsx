"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatPercent, formatNumber, getDefaultTimeRange, TAB_TRIGGER_CLASS } from "@/lib/constants";
import type { ResourceData, HAStatusData, InterfaceStatsData, InterfaceStatsItem } from "@/types";
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
type TabIndex = 0 | 1;

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
                      />
                      <ResourceAreaCard
                        title="Memory Usage (%)"
                        data={(resources?.timeline?.memory || []).filter(d => d.device === device)}
                        color="amber" valueFormatter={formatPercent}
                      />
                      <ResourceAreaCard
                        title="Active Sessions"
                        data={(resources?.timeline?.sessions || []).filter(d => d.device === device)}
                        color="purple" valueFormatter={formatNumber}
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
      <Tabs value={tabIndex === 0 ? "resources" : "bandwidth"} onValueChange={(val) => setTabIndex(val === "resources" ? 0 : 1)}>
        <TabsList className="mb-4 p-1 gap-1 bg-muted/40 dark:bg-muted/30 rounded-lg inline-flex">
          <TabsTrigger value="resources" className={TAB_TRIGGER_CLASS}>Resource Usage</TabsTrigger>
          <TabsTrigger value="bandwidth" className={TAB_TRIGGER_CLASS}>Interface Bandwidth</TabsTrigger>
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
                          />
                          <ResourceAreaCard
                            title="Memory Usage (%)"
                            data={(resources?.timeline?.memory || []).filter(d => d.device === device)}
                            color="amber" valueFormatter={formatPercent}
                          />
                          <ResourceAreaCard
                            title="Active Sessions"
                            data={(resources?.timeline?.sessions || []).filter(d => d.device === device)}
                            color="purple" valueFormatter={formatNumber}
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
                    <InterfaceBandwidthCard key={i} iface={iface} />
                  ))}
                </div>
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

// ── Interface Bandwidth Card ─────────────────────────────────────
function InterfaceBandwidthCard({ iface }: { iface: InterfaceStatsItem }) {
  const isUp = iface.oper_status === 1;

  const chartData = (iface.timeline || []).map((pt) => ({
    timestamp: new Date(pt.timestamp).toLocaleTimeString("en-US", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false, timeZone: "Asia/Jakarta",
    }),
    In: pt.in_mbps ?? 0,
    Out: pt.out_mbps ?? 0,
  }));

  const hasTimeline = chartData.length > 1;

  return (
    <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-semibold font-mono truncate" title={iface.label}>
            {iface.label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
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

      <div className="grid grid-cols-2 gap-3">
        <div className="bg-blue-50 dark:bg-blue-950/20 rounded-lg p-3 text-center">
          <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Inbound</p>
          <p className="text-lg font-bold text-blue-600 dark:text-blue-400">
            {iface.current_in_mbps != null
              ? iface.current_in_mbps >= 1000
                ? `${(iface.current_in_mbps / 1000).toFixed(1)} Gbps`
                : `${iface.current_in_mbps.toFixed(1)} Mbps`
              : "—"}
          </p>
        </div>
        <div className="bg-orange-50 dark:bg-orange-950/20 rounded-lg p-3 text-center">
          <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Outbound</p>
          <p className="text-lg font-bold text-orange-600 dark:text-orange-400">
            {iface.current_out_mbps != null
              ? iface.current_out_mbps >= 1000
                ? `${(iface.current_out_mbps / 1000).toFixed(1)} Gbps`
                : `${iface.current_out_mbps.toFixed(1)} Mbps`
              : "—"}
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
  title, data, color, valueFormatter,
}: {
  title: string; data: { timestamp: number; value: number }[];
  color: string; valueFormatter: (v: number) => string;
}) {
  if (!data || data.length === 0) {
    return (
      <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg p-3 shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
        <p className="text-xs font-medium mb-2">{title}</p>
        <p className="text-xs text-muted-foreground py-8 text-center">No data</p>
      </div>
    );
  }

  const chartData = data.map((d) => ({
    timestamp: new Date(d.timestamp).toLocaleTimeString("en-US", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false, timeZone: "Asia/Jakarta",
    }),
    value: d.value,
  }));

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
        />
      </div>
    </div>
  );
}
