"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatBytes, formatNumber, getDefaultTimeRange } from "@/lib/constants";
import type { OverviewData, TopASOrg, TopInboundService, WanInterfaceSummary, SiteWanBandwidth } from "@/types";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";

const SITE_OPTIONS = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"];
const SITE_LABELS: Record<string, string> = {
  "Site_FGT-DC": "DC",
  "Site_FGT-DRC": "DRC",
  "Site_FGT_Office": "Office",
};

// Sort: WAN first (LinkNet, iForte, LDP), then MPLS (LinkNet, iForte)
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
  const [appSite, setAppSite] = useState("Site_FGT-DC");
  const [asSite, setAsSite] = useState("Site_FGT-DC");
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

  const swrKey = token
    ? `/api/v1/overview?gte_ms=${currentGteMs}&lte_ms=${currentLteMs}`
    : null;

  const { data, error, isLoading } = useSWR<{ data: OverviewData; meta: any }>(
    swrKey, swrFetcher, { refreshInterval: 0 }
  );

  const overview = data?.data;

  // Per-site application fetches
  const appKey = token
    ? `/api/v1/traffic-flow/summary?site_name=${appSite}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}&path_filter=internet`
    : null;
  const { data: appData } = useSWR<{ data: any }>(appKey, swrFetcher, { refreshInterval: 0 });
  const appItems = appData?.data?.top_apps || [];

  // Per-site AS org fetches
  const asKey = token
    ? `/api/v1/traffic-flow/summary?site_name=${asSite}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}&path_filter=internet`
    : null;
  const { data: asData } = useSWR<{ data: any }>(asKey, swrFetcher, { refreshInterval: 0 });
  const asItems = asData?.data?.top_dst_as_org || [];

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

      {/* ═══ ROW 1 — KPI Cards ═══ */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <KpiCard title="SSL VPN" value={overview?.ssl_vpn_users?.active_users} subtitle="Active Users" loading={isLoading}
          onClick={() => router.push("/dashboard/vpn")} color="blue" />
        <KpiCard title="IPsec VPN" value={overview?.ipsec_vpn_users?.active_users} subtitle="Active Users" loading={isLoading}
          onClick={() => router.push("/dashboard/vpn")} color="emerald" />
        <KpiCard title="Throughput" value={overview?.total_throughput?.bytes_human} subtitle="Total Traffic" loading={isLoading}
          onClick={() => router.push("/dashboard/traffic")} color="violet" />
        <KpiCard title="Devices" value={overview?.devices?.length} subtitle="Online" loading={isLoading}
          onClick={() => router.push("/dashboard/resources")} color="amber" />
        <KpiCard title="HA Cluster" value={overview?.ha_status?.overall_health || "—"} subtitle={overview?.ha_status ? `${overview.ha_status.member_count} members` : "DC Only"} loading={isLoading}
          onClick={() => router.push("/dashboard/resources")} color={overview?.ha_status?.overall_health === "healthy" ? "emerald" : "red"} />
        <KpiCard title="Alerts" value={overview?.active_alert_count} subtitle="Unacknowledged" loading={isLoading}
          onClick={() => router.push("/dashboard/alerts")} color={overview?.active_alert_count ? "red" : "slate"} />
      </div>

      {/* ═══ ROW 2 — Traffic Overview ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top Applications with site selector */}
        <ClickCard onClick={() => router.push("/dashboard/traffic")}>
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-lg font-semibold">Top Applications</h2>
            <div className="flex items-center gap-2">
              <select value={appSite} onChange={(e) => setAppSite(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="px-2 py-1 text-xs rounded border bg-background">
                {SITE_OPTIONS.map(s => <option key={s} value={s}>{SITE_LABELS[s] || s}</option>)}
              </select>
              <span className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
            </div>
          </div>
          {isLoading ? <SkeletonBars count={5} /> : appItems.length > 0 ? (
            <div className="space-y-2">
              {appItems.filter((a: any) => a.app_name !== "app-0").slice(0, 7).map((app: any, i: number) => (
                <BarRow key={app.app_name} rank={i + 1} label={app.app_name} bytes={app.total_bytes} bytesHuman={formatBytes(app.total_bytes)}
                  max={appItems[0]?.total_bytes || 1} color="bg-primary" />
              ))}
            </div>
          ) : <EmptyText />}
        </ClickCard>

        {/* Top Destinations with site selector */}
        <ClickCard onClick={() => router.push("/dashboard/traffic")}>
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-lg font-semibold">Top Destinations (AS Org)</h2>
            <div className="flex items-center gap-2">
              <select value={asSite} onChange={(e) => setAsSite(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="px-2 py-1 text-xs rounded border bg-background">
                {SITE_OPTIONS.map(s => <option key={s} value={s}>{SITE_LABELS[s] || s}</option>)}
              </select>
              <span className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
            </div>
          </div>
          {isLoading ? <SkeletonBars count={5} /> : asItems.length > 0 ? (
            <div className="space-y-2">
              {asItems.slice(0, 7).map((org: any, i: number) => (
                <BarRow key={org.org_name} rank={i + 1} label={org.org_name} bytes={org.total_bytes} bytesHuman={formatBytes(org.total_bytes)}
                  max={asItems[0]?.total_bytes || 1} color="bg-emerald-500" />
              ))}
            </div>
          ) : <EmptyText />}
        </ClickCard>
      </div>

      {/* ═══ ROW 3 — Infrastructure ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Device Resources */}
        <ClickCard onClick={() => router.push("/dashboard/resources")}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Device Resources</h2>
            <span className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View all →</span>
          </div>
          {isLoading ? (
            <div className="space-y-3">{Array.from({ length: 2 }).map((_, i) => <SkeletonCard key={i} />)}</div>
          ) : overview?.devices?.length ? (
            <div className="grid grid-cols-1 gap-3">
              {overview.devices.map((dev, idx) => (
                <div key={dev.hostname || idx} className="p-3 bg-muted/30 rounded-lg space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold">{dev.hostname || dev.device}</span>
                    <span className={cn("px-2 py-0.5 text-xs font-medium rounded-full",
                      dev.sync_status === "In Sync" || dev.sync_status === "1" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                      : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400")}>
                      {dev.sync_status === "In Sync" || dev.sync_status === "1" ? "In Sync" : dev.sync_status || "Unknown"}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <MiniGauge label="CPU" value={dev.cpu_usage ?? 0} max={100} color="#3b82f6" />
                    <MiniGauge label="Memory" value={dev.mem_usage ?? 0} max={100} color="#f59e0b" />
                    <div className="text-center">
                      <p className="text-[10px] text-muted-foreground uppercase">Sessions</p>
                      <p className="text-sm font-bold">{formatNumber(dev.session_count ?? 0)}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : <EmptyText />}
        </ClickCard>

        {/* WAN Bandwidth per site */}
        <ClickCard onClick={() => router.push("/dashboard/resources")}>
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-lg font-semibold">WAN / MPLS Bandwidth</h2>
            <div className="flex items-center gap-2">
              <select value={wanSite} onChange={(e) => setWanSite(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="px-2 py-1 text-xs rounded border bg-background">
                {SITE_OPTIONS.map(s => <option key={s} value={s}>{SITE_LABELS[s] || s}</option>)}
              </select>
              <span className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
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
      </div>

      {/* ═══ ROW 4 — Connectivity ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* SD-WAN Link Status */}
        <ClickCard onClick={() => router.push("/dashboard/sdwan")}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">SD-WAN Link Status</h2>
            <span className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View details →</span>
          </div>
          {isLoading ? <SkeletonBars count={4} /> : overview?.sdwan_sites?.length ? (
            <div className="space-y-3">
              {overview.sdwan_sites.map((site) => (
                <div key={site.site}>
                  <h3 className="text-sm font-medium text-muted-foreground mb-1">{site.site}{site.device && <span className="ml-1 text-xs">({site.device})</span>}</h3>
                  <div className="grid grid-cols-2 gap-2">
                    {site.links?.map((link) => (
                      <div key={link.link} className="flex items-center justify-between p-2 bg-muted/50 rounded-md">
                        <span className="text-xs">{link.link_name}</span>
                        <span className={cn("px-2 py-0.5 text-xs font-medium rounded-full",
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

        {/* Inbound VIP Services */}
        <ClickCard onClick={() => router.push("/dashboard/traffic-inbound")}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Inbound VIP — Top Services</h2>
            <span className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">View details →</span>
          </div>
          {isLoading ? <SkeletonBars count={5} /> : overview?.inbound_vip_services?.length ? (
            <div className="space-y-2">
              {overview.inbound_vip_services.map((svc, i) => (
                <BarRow key={svc.service_name} rank={i + 1} label={svc.service_name} bytes={svc.total_bytes} bytesHuman={svc.bytes_human}
                  max={overview.inbound_vip_services[0]?.total_bytes || 1} color="bg-violet-500" />
              ))}
            </div>
          ) : <EmptyText />}
        </ClickCard>
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
      <span className="text-xs text-muted-foreground w-5">{rank}.</span>
      <div className="flex-1">
        <div className="flex justify-between text-sm mb-0.5">
          <span className="truncate max-w-[180px]" title={label}>{label}</span>
          <span className="font-medium text-xs">{bytesHuman}</span>
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
      <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      <div className="relative h-1.5 bg-muted rounded-full mt-1 mb-0.5">
        <div className={cn("h-full rounded-full transition-all", warn ? "bg-red-500" : "")} style={{ width: `${pct}%`, backgroundColor: warn ? undefined : color }} />
      </div>
      <p className={cn("text-xs font-bold", warn ? "text-red-600" : "")}>{value.toFixed(1)}%</p>
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
