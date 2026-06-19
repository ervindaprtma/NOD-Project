"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatBytes, formatNumber, getDefaultTimeRange } from "@/lib/constants";
import type { OverviewData, TopASOrg, WanInterfaceSummary, SiteWanBandwidth, ResourceData } from "@/types";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";

const SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"] as const;
const SITE_SHORT: Record<string, string> = {
  "Site_FGT-DC": "DC",
  "Site_FGT-DRC": "DRC",
  "Site_FGT_Office": "Office",
};

// ── Interface sort order (WAN first, MPLS second; vendor grouping) ──
const WAN_ORDER = ["WAN LinkNet", "WAN iForte", "WAN LDP"];
const MPLS_ORDER = ["MPLS LinkNet", "MPLS iForte"];
function sortInterfaces(ifaces: { label: string }[]) {
  return [...ifaces].sort((a, b) => {
    const isWanA = a.label.startsWith("WAN");
    const isWanB = b.label.startsWith("WAN");
    if (isWanA && !isWanB) return -1;
    if (!isWanA && isWanB) return 1;
    const orderA = isWanA ? WAN_ORDER.indexOf(a.label) : MPLS_ORDER.indexOf(a.label);
    const orderB = isWanB ? WAN_ORDER.indexOf(b.label) : MPLS_ORDER.indexOf(b.label);
    return (orderA === -1 ? 99 : orderA) - (orderB === -1 ? 99 : orderB);
  });
}

function formatMemGB(kb: number): string {
  return `${(kb / 1048576).toFixed(1)} GB`;
}

export default function OverviewPage() {
  const router = useRouter();
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [activePresetSeconds, setActivePresetSeconds] = useState(TIME_PRESETS[0].seconds);
  const [refreshInterval, setRefreshInterval] = useState(DEFAULT_REFRESH_MS);
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customRangeLabel, setCustomRangeLabel] = useState<string | null>(null);
  const prevIntervalRef = useRef(DEFAULT_REFRESH_MS);
  const [wanSite, setWanSite] = useState("Site_FGT-DC");

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

  // ── Overview data ──
  const swrKey = token
    ? `/api/v1/overview?gte_ms=${currentGteMs}&lte_ms=${currentLteMs}`
    : null;
  const { data, error, isLoading } = useSWR<{ data: OverviewData; meta: any }>(
    swrKey, swrFetcher, { refreshInterval: 0 }
  );
  const overview = data?.data;

  // ── Per-site Top Apps (ROW 2) ──
  const appKeys = SITES.map(s =>
    token ? `/api/v1/traffic-flow/summary?site_name=${s}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}&path_filter=internet` : null
  );
  const { data: appData0 } = useSWR<{ data: any }>(appKeys[0], swrFetcher, { refreshInterval: 0 });
  const { data: appData1 } = useSWR<{ data: any }>(appKeys[1], swrFetcher, { refreshInterval: 0 });
  const { data: appData2 } = useSWR<{ data: any }>(appKeys[2], swrFetcher, { refreshInterval: 0 });
  const appDatas = [appData0, appData1, appData2];

  // ── Per-site Top AS Org (ROW 3) ──
  const asKeys = SITES.map(s =>
    token ? `/api/v1/traffic-flow/summary?site_name=${s}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}&path_filter=internet` : null
  );
  const { data: asData0 } = useSWR<{ data: any }>(asKeys[0], swrFetcher, { refreshInterval: 0 });
  const { data: asData1 } = useSWR<{ data: any }>(asKeys[1], swrFetcher, { refreshInterval: 0 });
  const { data: asData2 } = useSWR<{ data: any }>(asKeys[2], swrFetcher, { refreshInterval: 0 });
  const asDatas = [asData0, asData1, asData2];

  // ── Per-site Device Health (ROW 4) ──
  const resKeys = SITES.map(s =>
    token ? `/api/v1/resources?site_name=${s}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}` : null
  );
  const { data: resData0 } = useSWR<{ data: ResourceData }>(resKeys[0], swrFetcher, { refreshInterval: 0 });
  const { data: resData1 } = useSWR<{ data: ResourceData }>(resKeys[1], swrFetcher, { refreshInterval: 0 });
  const { data: resData2 } = useSWR<{ data: ResourceData }>(resKeys[2], swrFetcher, { refreshInterval: 0 });
  const resDatas = [resData0, resData1, resData2];

  // ── Per-site Inbound VIP (ROW 6) ──
  const inboundSites = ["Site_FGT-DC", "Site_FGT-DRC"] as const;
  const inbKeys = inboundSites.map(s =>
    token ? `/api/v1/traffic-inbound/summary?site_name=${s}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}` : null
  );
  const { data: inbData0 } = useSWR<{ data: any }>(inbKeys[0], swrFetcher, { refreshInterval: 0 });
  const { data: inbData1 } = useSWR<{ data: any }>(inbKeys[1], swrFetcher, { refreshInterval: 0 });
  const inbDatas = [inbData0, inbData1];

  function selectPreset(preset: typeof TIME_PRESETS[0]) {
    const now = Date.now();
    setSelectedPreset(preset.label);
    setActivePresetSeconds(preset.seconds);
    setGteMs(now - preset.seconds * 1000);
    setLteMs(now);
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

  if (error) {
    return (
      <div className="space-y-4">
        <div className="p-6 bg-destructive/10 border border-destructive/20 rounded-lg text-center">
          <p className="text-destructive font-medium">Failed to load dashboard data</p>
          <p className="text-sm text-muted-foreground mt-1">Check backend connectivity</p>
          <button onClick={() => window.location.reload()} className="mt-3 px-4 py-2 text-sm bg-destructive text-white rounded-md">Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 bg-card border rounded-lg p-1">
            {TIME_PRESETS.map((preset) => (
              <button key={preset.label} onClick={() => selectPreset(preset)}
                className={cn("px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                  selectedPreset === preset.label ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>
                {preset.label}
              </button>
            ))}
            <button onClick={() => setShowCustomPicker(true)}
              className={cn("px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                selectedPreset === "custom" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}
              title={customRangeLabel || "Select custom date/time range"}>
              {selectedPreset === "custom" && customRangeLabel ? (customRangeLabel.length > 28 ? customRangeLabel.slice(0, 26) + "…" : customRangeLabel) : "Custom"}
            </button>
          </div>
          <select value={refreshInterval} onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="h-8 px-2 text-xs border rounded-md bg-card text-muted-foreground cursor-pointer">
            {REFRESH_INTERVALS.map((ri) => (
              <option key={ri.value} value={ri.value}>{ri.label === "Off" ? "⏸ Off" : `↻ ${ri.label}`}</option>
            ))}
          </select>
        </div>
      </div>

      <TimeRangePicker isOpen={showCustomPicker} onApply={handleCustomApply} onCancel={() => setShowCustomPicker(false)}
        initialGteMs={gteMs} initialLteMs={lteMs} />

      {/* ═══ ROW 1 — KPI Cards (5) ═══ */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        <KpiCard title="SSL VPN" value={overview?.ssl_vpn_users?.active_users} subtitle="Active Users" loading={isLoading}
          onClick={() => router.push("/dashboard/vpn")} color="blue" />
        <KpiCard title="IPsec VPN" value={overview?.ipsec_vpn_users?.active_users} subtitle="Active Users" loading={isLoading}
          onClick={() => router.push("/dashboard/vpn")} color="emerald" />
        <KpiCard title="Devices" value={overview?.fortigate_device_count ?? 0} subtitle="FortiGate Total" loading={isLoading}
          onClick={() => router.push("/dashboard/resources")} color="amber" />
        <KpiCard title="HA Cluster" value={overview?.ha_status?.overall_health || "—"} subtitle={overview?.ha_status ? `${overview.ha_status.member_count} members` : "DC Only"} loading={isLoading}
          onClick={() => router.push("/dashboard/resources")} color={overview?.ha_status?.overall_health === "healthy" ? "emerald" : "red"} />
        <KpiCard title="Alerts" value={overview?.active_alert_count} subtitle="Unacknowledged" loading={isLoading}
          onClick={() => router.push("/dashboard/alerts")} color={overview?.active_alert_count ? "red" : "slate"} />
      </div>

      {/* ═══ ROW 2 — Top Applications (3 sites) ═══ */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {SITES.map((site, idx) => (
          <ClickCard key={site} onClick={() => router.push("/dashboard/traffic")}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Top Apps — {SITE_SHORT[site]}</h2>
              <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
            </div>
            {isLoading ? <SkeletonBars count={5} /> : (() => {
              const items = appDatas[idx]?.data?.top_apps || [];
              if (items.length === 0) return <EmptyText />;
              return (
                <div className="space-y-1.5">
                  {items.filter((a: any) => a.app_name !== "app-0").slice(0, 7).map((app: any, i: number) => (
                    <BarRow key={app.app_name || i} rank={i + 1} label={app.app_name} bytes={app.total_bytes} bytesHuman={formatBytes(app.total_bytes)}
                      max={items[0]?.total_bytes || 1} color="bg-primary" />
                  ))}
                </div>
              );
            })()}
          </ClickCard>
        ))}
      </div>

      {/* ═══ ROW 3 — Top Destinations AS Org (3 sites) ═══ */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {SITES.map((site, idx) => (
          <ClickCard key={site} onClick={() => router.push("/dashboard/traffic")}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Top AS Org — {SITE_SHORT[site]}</h2>
              <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
            </div>
            {isLoading ? <SkeletonBars count={5} /> : (() => {
              const items = asDatas[idx]?.data?.top_dst_as_org || [];
              if (items.length === 0) return <EmptyText />;
              return (
                <div className="space-y-1.5">
                  {items.slice(0, 5).map((org: any, i: number) => (
                    <BarRow key={org.org_name || i} rank={i + 1} label={org.org_name} bytes={org.total_bytes} bytesHuman={formatBytes(org.total_bytes)}
                      max={items[0]?.total_bytes || 1} color="bg-emerald-500" />
                  ))}
                </div>
              );
            })()}
          </ClickCard>
        ))}
      </div>

      {/* ═══ ROW 4 — Device Health (3 sites) ═══ */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {SITES.map((site, idx) => (
          <ClickCard key={site} onClick={() => router.push("/dashboard/resources")}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Device Health — {SITE_SHORT[site]}</h2>
              <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View details →</span>
            </div>
            {isLoading ? (
              <div className="space-y-2">{Array.from({ length: 2 }).map((_, i) => <SkeletonCard key={i} />)}</div>
            ) : (() => {
              const current = resDatas[idx]?.data?.current || [];
              if (current.length === 0) return <EmptyText />;
              const isDC = site === "Site_FGT-DC";
              return (
                <div className="space-y-2">
                  {current.map((dev, i) => (
                    <div key={dev.device || i} className="p-2.5 bg-muted/30 rounded-lg space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold">
                          {isDC ? (i === 0 ? "PRIMARY" : "SECONDARY") : SITE_SHORT[site]}
                          {dev.hostname && <span className="text-[10px] text-muted-foreground ml-1">({dev.hostname})</span>}
                        </span>
                        <span className={cn("px-1.5 py-0.5 text-[10px] font-medium rounded-full",
                          dev.sync_status === "In Sync" || dev.sync_status === "1" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : dev.sync_status === "standalone" ? "bg-slate-100 text-slate-600 dark:bg-slate-800/30 dark:text-slate-400"
                          : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400")}>
                          {dev.sync_status === "In Sync" || dev.sync_status === "1" ? "In Sync"
                            : dev.sync_status === "standalone" ? "Standalone"
                            : dev.sync_status || "Unknown"}
                        </span>
                      </div>
                      <div className="grid grid-cols-3 gap-1.5">
                        <MiniGauge label="CPU" value={dev.cpu_usage ?? 0} max={100} color="#3b82f6" />
                        <MiniGauge label="Mem" value={dev.mem_usage ?? 0} max={100} color="#f59e0b" />
                        <div className="text-center">
                          <p className="text-[9px] text-muted-foreground uppercase">Sessions</p>
                          <p className="text-xs font-bold">{formatNumber(dev.session_count ?? 0)}</p>
                        </div>
                      </div>
                      {(dev.serial_number || dev.mem_capacity_kb) && (
                        <div className="text-[10px] text-muted-foreground space-y-0.5">
                          {dev.serial_number && <p>S/N: {dev.serial_number}</p>}
                          {dev.mem_capacity_kb ? <p>RAM: {formatMemGB(dev.mem_capacity_kb)}</p> : null}
                        </div>
                      )}
                    </div>
                  ))}
                  {/* HA Cluster Status (DC only) */}
                  {isDC && overview?.ha_status && (
                    <div className="p-2.5 bg-muted/20 rounded-lg border border-dashed border-border/50">
                      <p className="text-[10px] font-semibold text-muted-foreground uppercase mb-1">HA Cluster Status</p>
                      <div className="grid grid-cols-2 gap-1 text-[11px]">
                        <div>
                          <span className="text-muted-foreground">Mode: </span>
                          <span className="font-medium">{overview.ha_status.ha_mode}</span>
                        </div>
                        <div>
                          <span className="text-muted-foreground">Health: </span>
                          <span className={cn("font-medium",
                            overview.ha_status.overall_health === "healthy" ? "text-emerald-600" : "text-red-600")}>
                            {overview.ha_status.overall_health === "healthy" ? "● " : ""}
                            {overview.ha_status.overall_health}
                          </span>
                        </div>
                        <div>
                          <span className="text-muted-foreground">Members: </span>
                          <span className="font-medium">{overview.ha_status.member_count}</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}
          </ClickCard>
        ))}
      </div>

      {/* ═══ ROW 5 — WAN/MPLS Bandwidth + SD-WAN ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* WAN/MPLS Bandwidth */}
        <ClickCard onClick={() => router.push("/dashboard/resources")}>
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-sm font-semibold">WAN/MPLS Bandwidth</h2>
            <div className="flex items-center gap-2">
              <select value={wanSite} onChange={(e) => setWanSite(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="px-2 py-1 text-xs rounded border bg-background">
                {SITES.map(s => <option key={s} value={s}>{SITE_SHORT[s] || s}</option>)}
              </select>
              <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
            </div>
          </div>
          {isLoading ? <SkeletonBars count={4} /> : (() => {
            const siteData = overview?.wan_bandwidth?.find((s: SiteWanBandwidth) => s.site === wanSite);
            if (!siteData?.interfaces?.length) return <EmptyText />;
            return (
              <div className="grid grid-cols-2 gap-2">
                {sortInterfaces(siteData.interfaces).map((iface: WanInterfaceSummary, i: number) => (
                  <div key={i} className="p-2 bg-muted/30 rounded-lg">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-semibold truncate">{iface.label}</span>
                      <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full font-medium",
                        iface.oper_status === "UP" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400")}>
                        {iface.oper_status || "?"}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-1 text-center text-[11px]">
                      <div className="bg-blue-50 dark:bg-blue-950/20 rounded px-1 py-0.5">
                        <span className="text-muted-foreground">In </span>
                        <span className="font-bold text-blue-600 dark:text-blue-400">{iface.in_mbps != null ? iface.in_mbps.toFixed(1) : "—"}</span>
                        <span className="text-[9px] text-muted-foreground"> Mbps</span>
                      </div>
                      <div className="bg-orange-50 dark:bg-orange-950/20 rounded px-1 py-0.5">
                        <span className="text-muted-foreground">Out </span>
                        <span className="font-bold text-orange-600 dark:text-orange-400">{iface.out_mbps != null ? iface.out_mbps.toFixed(1) : "—"}</span>
                        <span className="text-[9px] text-muted-foreground"> Mbps</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            );
          })()}
        </ClickCard>

        {/* SD-WAN Link Status */}
        <ClickCard onClick={() => router.push("/dashboard/sdwan")}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold">SD-WAN Link Status</h2>
            <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View details →</span>
          </div>
          {isLoading ? <SkeletonBars count={4} /> : overview?.sdwan_sites?.length ? (
            <div className="space-y-3">
              {overview.sdwan_sites.map((site) => (
                <div key={site.site}>
                  <h3 className="text-xs font-medium text-muted-foreground mb-1">{SITE_SHORT[site.site] || site.site}{site.device && <span className="ml-1 text-[10px]">({site.device})</span>}</h3>
                  <div className="grid grid-cols-2 gap-1.5">
                    {site.links?.map((link) => (
                      <div key={link.link} className="flex items-center justify-between p-1.5 bg-muted/50 rounded-md">
                        <span className="text-[11px]">{link.link_name}</span>
                        <span className={cn("px-1.5 py-0.5 text-[10px] font-medium rounded-full",
                          link.status === "Up" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400")}>{link.status}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : <EmptyText />}
        </ClickCard>
      </div>

      {/* ═══ ROW 6 — Inbound VIP (DC + DRC) ═══ */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {inboundSites.map((site, idx) => (
          <ClickCard key={site} onClick={() => router.push("/dashboard/traffic-inbound")}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Inbound VIP — {SITE_SHORT[site]}</h2>
              <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
            </div>
            {isLoading ? <SkeletonBars count={5} /> : (() => {
              const items = inbDatas[idx]?.data?.top_services || [];
              if (items.length === 0) return <EmptyText />;
              return (
                <div className="space-y-1.5">
                  {items.slice(0, 5).map((svc: any, i: number) => (
                    <BarRow key={svc.service_name || svc.service || i} rank={i + 1} label={svc.service_name || svc.service} bytes={svc.total_bytes} bytesHuman={formatBytes(svc.total_bytes)}
                      max={items[0]?.total_bytes || 1} color="bg-violet-500" />
                  ))}
                </div>
              );
            })()}
          </ClickCard>
        ))}
      </div>

      {/* ═══ ROW 7 — Top Customer AS — Inbound VIP (DC + DRC) ═══ */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {inboundSites.map((site, idx) => (
          <ClickCard key={`cas-${site}`} onClick={() => router.push("/dashboard/traffic-inbound")}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Top Customer AS — {SITE_SHORT[site]}</h2>
              <span className="text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
            </div>
            {isLoading ? <SkeletonBars count={5} /> : (() => {
              const items = inbDatas[idx]?.data?.top_src_as_org || [];
              if (items.length === 0) return <EmptyText />;
              return (
                <div className="space-y-1.5">
                  {items.slice(0, 7).map((org: any, i: number) => (
                    <BarRow key={org.org_name || i} rank={i + 1} label={org.org_name} bytes={org.total_bytes} bytesHuman={formatBytes(org.total_bytes)}
                      max={items[0]?.total_bytes || 1} color="bg-cyan-500" />
                  ))}
                </div>
              );
            })()}
          </ClickCard>
        ))}
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────

function KpiCard({ title, value, subtitle, loading, onClick, color }: {
  title: string; value?: string | number; subtitle?: string; loading?: boolean; onClick?: () => void; color: string;
}) {
  const colors: Record<string, string> = {
    blue: "border-l-blue-500", emerald: "border-l-emerald-500", violet: "border-l-violet-500",
    amber: "border-l-amber-500", red: "border-l-red-500", slate: "border-l-slate-400",
  };
  return (
    <div onClick={onClick}
      className={cn("p-4 bg-card border rounded-lg border-l-4 transition-all", colors[color] || colors.blue,
        onClick && "cursor-pointer hover:shadow-md hover:border-l-[6px]")}>
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{title}</h3>
      {loading ? <div className="mt-2 h-8 w-16 bg-muted animate-pulse rounded" />
        : <p className="mt-1 text-2xl font-bold">{value ?? "—"}</p>}
      {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
    </div>
  );
}

function ClickCard({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) {
  return (
    <div onClick={onClick}
      className={cn("p-4 bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 group",
        onClick && "cursor-pointer hover:border-primary/40 hover:shadow-md transition-all")}>
      {children}
    </div>
  );
}

function BarRow({ rank, label, bytes, bytesHuman, max, color }: {
  rank: number; label: string; bytes: number; bytesHuman: string; max: number; color: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground w-4">{rank}.</span>
      <div className="flex-1">
        <div className="flex justify-between text-sm mb-0.5">
          <span className="truncate max-w-[160px] text-xs" title={label}>{label}</span>
          <span className="font-medium text-[11px]">{bytesHuman}</span>
        </div>
        <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
          <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${(bytes / max) * 100}%` }} />
        </div>
      </div>
    </div>
  );
}

function MiniGauge({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  const warn = pct > 80;
  return (
    <div className="text-center">
      <p className="text-[9px] text-muted-foreground uppercase">{label}</p>
      <div className="relative h-1.5 bg-muted rounded-full mt-0.5 mb-0.5">
        <div className={cn("h-full rounded-full transition-all", warn ? "bg-red-500" : "")} style={{ width: `${pct}%`, backgroundColor: warn ? undefined : color }} />
      </div>
      <p className={cn("text-[10px] font-bold", warn ? "text-red-600" : "")}>{value.toFixed(1)}%</p>
    </div>
  );
}

function SkeletonBars({ count }: { count: number }) {
  return <div className="space-y-2">{Array.from({ length: count }).map((_, i) => <div key={i} className="h-5 bg-muted animate-pulse rounded w-full" />)}</div>;
}

function SkeletonCard() {
  return <div className="p-3 bg-muted/30 rounded-lg animate-pulse space-y-2"><div className="h-4 bg-muted rounded w-24" /><div className="grid grid-cols-3 gap-2"><div className="h-10 bg-muted rounded" /><div className="h-10 bg-muted rounded" /><div className="h-10 bg-muted rounded" /></div></div>;
}

function EmptyText() {
  return <p className="text-xs text-muted-foreground py-6 text-center">No data available</p>;
}
