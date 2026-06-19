"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import useSWR from "swr";
import * as d3Sankey from "d3-sankey";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatBytes, getDefaultTimeRange, TAB_TRIGGER_CLASS } from "@/lib/constants";
import type { TrafficInternalSummary, TrafficInternalChartData, TrafficInboundTableData, TrafficInboundTableRecord, SankeyResponse } from "@/types";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";
import { AreaChart } from "@/components/charts/AreaChart";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@radix-ui/react-tabs";

const SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"];
const SITE_LABELS: Record<string, string> = { "Site_FGT-DC": "DC", "Site_FGT-DRC": "DRC", "Site_FGT_Office": "Office" };
const TRAFFIC_PATHS = ["all", "intra-lan", "inter-site"] as const;
const TRAFFIC_PATH_LABELS: Record<string, string> = { "all": "All Paths", "intra-lan": "Intra-LAN", "inter-site": "Inter-Site" };

interface FilterState { application: string; client_ip: string; server_ip: string; protocol: string; dst_port: string; }
const defaultFilters: FilterState = { application: "", client_ip: "", server_ip: "", protocol: "", dst_port: "" };

function countActiveFilters(f: FilterState): number {
  let n = 0;
  if (f.application) n++; if (f.client_ip) n++; if (f.server_ip) n++; if (f.protocol) n++; if (f.dst_port) n++;
  return n;
}

export default function TrafficInternalPage() {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [activePresetSeconds, setActivePresetSeconds] = useState(TIME_PRESETS[0].seconds);
  const [siteName, setSiteName] = useState("Site_FGT-DC");
  const [trafficPath, setTrafficPath] = useState<string>("all");
  const [refreshInterval, setRefreshInterval] = useState(DEFAULT_REFRESH_MS);
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customRangeLabel, setCustomRangeLabel] = useState<string | null>(null);
  const prevIntervalRef = useRef(DEFAULT_REFRESH_MS);

  const [filters, setFilters] = useState<FilterState>(defaultFilters);
  const [draftFilters, setDraftFilters] = useState<FilterState>(defaultFilters);
  const [showFilters, setShowFilters] = useState(false);

  const token = typeof window !== "undefined" ? getAccessToken() : null;
  const [currentGteMs, setCurrentGteMs] = useState(defaultRange.gte_ms);
  const [currentLteMs, setCurrentLteMs] = useState(defaultRange.lte_ms);

  const bucketSeconds = useMemo(() => {
    const rangeMs = currentLteMs - currentGteMs;
    const rangeSec = Math.max(60, Math.floor(rangeMs / 1000));
    return Math.max(60, Math.ceil(rangeSec / 30));
  }, [currentGteMs, currentLteMs]);

  useEffect(() => {
    if (activePresetSeconds <= 0) { setCurrentGteMs(gteMs); setCurrentLteMs(lteMs); return; }
    const tick = () => { const now = Date.now(); setCurrentGteMs(now - activePresetSeconds * 1000); setCurrentLteMs(now); };
    tick();
    const id = setInterval(tick, refreshInterval > 0 ? refreshInterval : 60_000);
    return () => clearInterval(id);
  }, [activePresetSeconds, refreshInterval, gteMs, lteMs]);

  const filterQS = useMemo(() => {
    const parts: string[] = [`traffic_path=${trafficPath}`];
    if (filters.application) parts.push(`app_filter=${encodeURIComponent(filters.application)}`);
    if (filters.client_ip) parts.push(`client_ip=${encodeURIComponent(filters.client_ip)}`);
    if (filters.server_ip) parts.push(`server_ip=${encodeURIComponent(filters.server_ip)}`);
    if (filters.protocol) parts.push(`protocol=${encodeURIComponent(filters.protocol)}`);
    if (filters.dst_port) parts.push(`dst_port=${filters.dst_port}`);
    return "&" + parts.join("&");
  }, [filters, trafficPath]);

  const summaryKey = token ? `/api/v1/traffic-internal/summary?site_name=${siteName}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}${filterQS}` : null;
  const chartKey = token ? `/api/v1/traffic-internal/chart?site_name=${siteName}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}&bucket_seconds=${bucketSeconds}${filterQS}` : null;
  const tableKey = token ? `/api/v1/traffic-internal/table?site_name=${siteName}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}${filterQS}` : null;
  const sankeyKey = token ? `/api/v1/traffic-internal/sankey?site_name=${siteName}&gte_ms=${currentGteMs}&lte_ms=${currentLteMs}${filterQS}` : null;

  const { data: summaryEnv, error: summaryError, isLoading: summaryLoading } = useSWR<{ success: boolean; data: TrafficInternalSummary; meta: any }>(summaryKey, swrFetcher, { refreshInterval: 0 });
  const { data: chartEnv, error: chartError, isLoading: chartLoading } = useSWR<{ data: TrafficInternalChartData }>(chartKey, swrFetcher, { refreshInterval: 0 });
  const { data: tableEnv, error: tableError, isLoading: tableLoading } = useSWR<{ data: TrafficInboundTableData }>(tableKey, swrFetcher, { refreshInterval: 0 });
  const { data: sankeyEnv, error: sankeyError, isLoading: sankeyLoading } = useSWR<{ success: boolean; data: SankeyResponse }>(sankeyKey, swrFetcher, { refreshInterval: 0 });

  const summary: TrafficInternalSummary | undefined = summaryEnv?.data;
  const chart: TrafficInternalChartData | undefined = chartEnv?.data;
  const table: TrafficInboundTableData | undefined = tableEnv?.data;
  const queryTook = summaryEnv?.meta?.query_took_ms;
  const sankeyData: SankeyResponse | undefined = sankeyEnv?.data;
  const hasError = summaryError || chartError || tableError;

  const [activeTab, setActiveTab] = useState<"overview" | "sankey">("overview");

  const throughputTimeline = useMemo(() => {
    if (!chart?.chart_data) return [];
    return chart.chart_data.map((row: Record<string, any>) => {
      let totalBytes = 0;
      for (const svc of chart.service_names || []) totalBytes += Number(row[svc]) || 0;
      const ms = row.timestampMs || (row.timestamp ? new Date(row.timestamp).getTime() : 0);
      const mbps = parseFloat(((totalBytes * 8) / bucketSeconds / 1_000_000).toFixed(2));
      return { timestamp: ms ? new Date(ms).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Jakarta" }) : row.timestamp, mbps };
    });
  }, [chart, bucketSeconds]);

  function handlePreset(seconds: number, label: string) {
    const now = Date.now(); setGteMs(now - seconds * 1000); setLteMs(now);
    setActivePresetSeconds(seconds); setSelectedPreset(label); setCustomRangeLabel(null); setShowCustomPicker(false);
    setRefreshInterval((prev) => prev === 0 ? prevIntervalRef.current : prev);
  }

  function handleCustomApply(range: CustomTimeRange) {
    setGteMs(range.gte_ms); setLteMs(range.lte_ms); setActivePresetSeconds(0); setSelectedPreset("custom");
    prevIntervalRef.current = refreshInterval > 0 ? refreshInterval : DEFAULT_REFRESH_MS; setRefreshInterval(0); setShowCustomPicker(false);
    const from = new Date(range.gte_ms); const to = new Date(range.lte_ms);
    setCustomRangeLabel(`${from.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "Asia/Jakarta" })} ${from.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" })} — ${to.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "Asia/Jakarta" })} ${to.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" })}`);
  }

  function applyFilters() { setFilters({ ...draftFilters }); }
  function resetFilters() { setDraftFilters(defaultFilters); setFilters(defaultFilters); }

  const activeFilterCount = countActiveFilters(filters);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Traffic Internal</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <select value={siteName} onChange={(e) => setSiteName(e.target.value)}
            className="px-3 py-1.5 rounded-md border border-border/60 dark:border-border/40 bg-background text-sm shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
            {SITES.map((s) => <option key={s} value={s}>{SITE_LABELS[s] || s}</option>)}
          </select>
          <select value={trafficPath} onChange={(e) => setTrafficPath(e.target.value)}
            className="px-3 py-1.5 rounded-md border border-border/60 dark:border-border/40 bg-background text-sm shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
            {TRAFFIC_PATHS.map((p) => <option key={p} value={p}>{TRAFFIC_PATH_LABELS[p]}</option>)}
          </select>
          <div className="flex gap-1 bg-muted rounded-md p-1">
            {TIME_PRESETS.map((p) => (
              <button key={p.label} onClick={() => handlePreset(p.seconds, p.label)}
                className={cn("px-2.5 py-1 text-xs rounded-sm transition-colors",
                  selectedPreset === p.label ? "bg-background text-foreground shadow ring-1 ring-black/5 dark:ring-white/20"
                  : "text-muted-foreground hover:text-foreground hover:bg-background/50 dark:hover:bg-background/20")}>{p.label}</button>
            ))}
            <button onClick={() => setShowCustomPicker(true)}
              className={cn("px-2.5 py-1 text-xs rounded-sm transition-colors",
                selectedPreset === "custom" ? "bg-background text-foreground shadow ring-1 ring-black/5 dark:ring-white/20"
                : "text-muted-foreground hover:text-foreground hover:bg-background/50 dark:hover:bg-background/20")}
              title={customRangeLabel || "Select custom date/time range"}>
              {selectedPreset === "custom" && customRangeLabel ? (customRangeLabel.length > 24 ? customRangeLabel.slice(0, 22) + "…" : customRangeLabel) : "Custom"}
            </button>
          </div>
          <select value={refreshInterval} onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="h-7 px-2 text-xs border border-border/60 dark:border-border/40 rounded-md bg-card text-muted-foreground cursor-pointer shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
            {REFRESH_INTERVALS.map((ri) => <option key={ri.value} value={ri.value}>{ri.label === "Off" ? "⏸ Off" : `↻ ${ri.label}`}</option>)}
          </select>
          {queryTook != null && <span className="text-xs text-muted-foreground">{queryTook}ms</span>}
        </div>
      </div>

      {hasError && <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">Failed to load. <button onClick={() => window.location.reload()} className="underline">Retry</button></div>}

      {/* Filter bar */}
      <div className="border border-border/60 dark:border-border/40 rounded-lg bg-card shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20">
        <button onClick={() => setShowFilters(!showFilters)} className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium hover:bg-muted/30 transition-colors rounded-t-lg">
          <span>🔍 Filters{activeFilterCount > 0 && <span className="ml-2 px-1.5 py-0.5 text-xs bg-primary text-primary-foreground rounded-full">{activeFilterCount} active</span>}</span>
          <span className={cn("text-xs text-muted-foreground transition-transform", showFilters && "rotate-180")}>▼</span>
        </button>
        {showFilters && (
          <div className="px-4 pb-4 pt-1 border-t border-muted/40">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
              <FilterField label="Service Name" value={draftFilters.application} onChange={(v) => setDraftFilters({ ...draftFilters, application: v })} />
              <FilterField label="Client IP" value={draftFilters.client_ip} onChange={(v) => setDraftFilters({ ...draftFilters, client_ip: v })} mono />
              <FilterField label="Server IP" value={draftFilters.server_ip} onChange={(v) => setDraftFilters({ ...draftFilters, server_ip: v })} mono />
              <FilterField label="Protocol" value={draftFilters.protocol} onChange={(v) => setDraftFilters({ ...draftFilters, protocol: v.toUpperCase() })} />
              <FilterField label="Dst Port" value={draftFilters.dst_port} onChange={(v) => setDraftFilters({ ...draftFilters, dst_port: v })} />
            </div>
            <div className="flex items-center gap-2 mt-3">
              <button onClick={applyFilters} className="px-4 py-1.5 text-xs font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">Apply</button>
              <button onClick={resetFilters} className="px-4 py-1.5 text-xs font-medium border rounded-md hover:bg-muted transition-colors">Reset</button>
            </div>
          </div>
        )}
      </div>

      <Tabs defaultValue="overview">
        <TabsList className="mb-6 p-1 gap-1 bg-muted/40 dark:bg-muted/30 rounded-lg inline-flex"><TabsTrigger value="overview" className={TAB_TRIGGER_CLASS}>Overview</TabsTrigger><TabsTrigger value="sankey" className={TAB_TRIGGER_CLASS}>Sankey Diagram</TabsTrigger></TabsList>
        <TabsContent value="overview">
            {!summaryLoading && !chartLoading && !tableLoading && !hasError && !summary && (
              <div className="p-4 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-300 text-sm">No internal traffic data for {SITE_LABELS[siteName] || siteName}.</div>
            )}

            {/* ROW 1 — Top Services (full width) */}
            <div className="mb-6">
              <RankedCard title="Top Services" loading={summaryLoading} error={!!summaryError}
                items={(summary?.top_services || []).filter(s => s.service_name !== "Port-0").slice(0, 10).map(s => ({ name: s.service_name, value: s.total_bytes }))} color="blue" wide />
            </div>

            {/* ROW 2 — 4 medium cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
              <RankedCard title="Top Client IPs" loading={summaryLoading} error={!!summaryError}
                items={(summary?.top_clients || []).slice(0, 10).map(c => ({ name: c.ip, value: c.total_bytes, mono: true }))} color="violet" />
              <RankedCard title="Top Server IPs" loading={summaryLoading} error={!!summaryError}
                items={(summary?.top_servers || []).slice(0, 10).map(s => ({ name: s.ip + (s.hostname ? ` (${s.hostname})` : ""), value: s.total_bytes, mono: true }))} color="pink" />
              <RankedCard title="Ingress Interfaces" loading={summaryLoading} error={!!summaryError}
                items={(summary?.ingress_breakdown || []).slice(0, 10).map(e => ({ name: e.interface, value: e.total_bytes, mono: true }))} color="emerald" />
              <RankedCard title="Egress Interfaces" loading={summaryLoading} error={!!summaryError}
                items={(summary?.egress_breakdown || []).slice(0, 10).map(e => ({ name: e.interface, value: e.total_bytes, mono: true }))} color="orange" />
            </div>

            {/* ROW 3 — Protocol Distribution */}
            <div className="mb-6">
              <RankedCard title="Protocol Distribution" loading={summaryLoading} error={!!summaryError}
                items={(summary?.protocol_dist || []).map(p => ({ name: p.protocol, value: p.total_bytes }))} color="cyan" wide />
            </div>

            {/* ROW 4 — Charts */}
            <div className="space-y-4 mb-6">
              <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6">
                <h2 className="text-lg font-semibold mb-3">Total Throughput Over Time</h2>
                {chartLoading ? <SkeletonChart /> : chartError ? <ErrorText /> : throughputTimeline.length > 0 ? (
                    <AreaChart data={throughputTimeline} categories={["mbps"]} index="timestamp" colors={["#3b82f6"]} showLegend={false} showGridLines={true} showXAxis={true} showYAxis={true} className="h-72" />
                  ) : <EmptyState message="No throughput data" />}
              </div>
              <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6">
                <h2 className="text-lg font-semibold mb-1">Service Throughput — {bucketSeconds}s Buckets</h2>
                {chartLoading ? <SkeletonChart /> : chartError ? <ErrorText /> : chart?.chart_data?.length ? (
                  <StackedBarChart data={chart.chart_data.map((row) => { const entry: Record<string, any> = { timestamp: row.timestampMs || row.timestamp }; for (const svc of chart.service_names || []) { entry[svc] = parseFloat(((Number(row[svc]) || 0) * 8 / bucketSeconds / 1_000_000).toFixed(2)); } return entry; })} serviceNames={chart.service_names || []} />
                ) : <EmptyState message="No service throughput data" />}
              </div>
            </div>

            {/* ROW 5 — Table */}
            <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6">
              <h2 className="text-lg font-semibold mb-3">Flow Records</h2>
              {tableLoading ? <div className="space-y-2">{[1,2,3,4,5].map((i) => <div key={i} className="h-8 bg-muted rounded animate-pulse" />)}</div>
                : tableError ? <ErrorText /> : table?.records?.length ? <FlowRecordsTable records={table.records.slice(0, 25)} />
                : <EmptyState message="No flow records found" />}
            </div>
          </TabsContent>

          <TabsContent value="sankey">
            <h3 className="text-base font-semibold text-slate-200 mb-2">Internal Traffic Flow</h3>
            <SankeyView data={sankeyData} loading={sankeyLoading} error={!!sankeyError} />
          </TabsContent>
        </Tabs>

      <TimeRangePicker isOpen={showCustomPicker} onApply={handleCustomApply} onCancel={() => setShowCustomPicker(false)} initialGteMs={gteMs} initialLteMs={lteMs} />
    </div>
  );
}

// ── Shared components (same as traffic page) ──────────────────────

function FilterField({ label, value, onChange, mono }: { label: string; value: string; onChange: (v: string) => void; mono?: boolean }) {
  return <div className="flex flex-col gap-1"><label className="text-[10px] text-muted-foreground uppercase font-medium">{label}</label><input type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder="..." className={cn("px-2 py-1.5 text-xs rounded border border-border/60 dark:border-border/40 bg-background focus:outline-none focus:ring-1 focus:ring-primary/30", mono && "font-mono text-[11px]")} /></div>;
}

const LEVEL_COLORS: Record<number, string> = { 0: "#3b82f6", 1: "#22c55e", 2: "#f97316" };

function SankeyView({ data, loading, error }: { data: SankeyResponse | undefined; loading: boolean; error: boolean }) {
  const svgRef = useRef<SVGSVGElement>(null);
  useEffect(() => {
    if (!data || !svgRef.current) return;
    const rawNodes = data.nodes.filter((n) => data.links.some((l) => (l.source === n.id || l.target === n.id) && l.value > 0));
    const rawLinks = data.links.filter((l) => l.value > 0);
    if (rawNodes.length === 0) return;
    const nodeById = new Map<number, number>(); rawNodes.forEach((n, i) => { nodeById.set(n.id, i); });
    interface SN { id: number; label: string; level: number; x0?: number; x1?: number; y0?: number; y1?: number; value?: number; }
    interface SL { source: number; target: number; value: number; width?: number; y0?: number; y1?: number; }
    const nodes: SN[] = rawNodes.map((n) => ({ ...n }));
    const links: SL[] = rawLinks.map((l) => ({ source: nodeById.get(l.source) ?? 0, target: nodeById.get(l.target) ?? 0, value: l.value }));
    const W = 800, H = 400;
    const sankey = (d3Sankey as any).sankey().nodeWidth(20).nodePadding(16).extent([[8, 8], [W - 8, H - 8]]).nodeSort((a: any, b: any) => { if (a.level !== b.level) return a.level - b.level; return (b.value ?? 0) - (a.value ?? 0); });
    const scaleMB = (v: number) => v / (1024 * 1024);
    const graph = sankey({ nodes: nodes.map(n => ({ ...n, value: Math.max(0.1, scaleMB(n.value ?? 0)) })), links: links.map(l => ({ ...l, value: Math.max(0.1, scaleMB(l.value)) })) }) as { nodes: SN[]; links: SL[] };
    const svgEl = svgRef.current; svgEl.innerHTML = "";
    const NS = "http://www.w3.org/2000/svg";
    const ce = (t: string, a: Record<string, any> = {}) => { const e = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) if (v != null) e.setAttribute(k, String(v)); return e; };
    const linkPath = d3Sankey.sankeyLinkHorizontal();
    for (const link of graph.links) {
      const src = (link as any).source as SN, tgt = (link as any).target as SN;
      if (!src || !tgt) continue;
      const g = ce("g");
      const p = ce("path", { d: linkPath(link as any), fill: "none", stroke: LEVEL_COLORS[src.level] || "#6b7280", "stroke-opacity": "0.4", "stroke-width": Math.max(1, (link as any).width ?? 1) });
      const t = ce("title"); t.textContent = `${src.label} → ${tgt.label}: ${link.value.toFixed(1)} MB`; p.appendChild(t); g.appendChild(p);
      if (((link as any).width ?? 0) > 3 && link.value > 0.5) {
        const mx = ((src.x1 ?? 0) + (tgt.x0 ?? 0)) / 2, my = ((link as any).y0 + (link as any).y1) / 2;
        const txt = ce("text", { x: mx, y: my, dy: "0.35em", "text-anchor": "middle", "font-size": ((link as any).width ?? 0) > 8 ? "10" : "8", fill: "currentColor", class: "fill-muted-foreground" });
        txt.textContent = link.value >= 1000 ? `${(link.value / 1000).toFixed(1)} GB` : `${link.value.toFixed(1)} MB`; g.appendChild(txt);
      }
      svgEl.appendChild(g);
    }
    for (const node of graph.nodes) {
      if (!node || (node.x1 ?? 0) - (node.x0 ?? 0) <= 0 || (node.y1 ?? 0) - (node.y0 ?? 0) <= 0) continue;
      const g = ce("g");
      const r = ce("rect", { x: node.x0 ?? 0, y: node.y0 ?? 0, width: (node.x1 ?? 0) - (node.x0 ?? 0), height: (node.y1 ?? 0) - (node.y0 ?? 0), fill: LEVEL_COLORS[node.level] || "#6b7280", stroke: LEVEL_COLORS[node.level] || "#6b7280", "stroke-width": "1" });
      const t = ce("title"); t.textContent = `${node.label}\n${(node.value ?? 0).toFixed(1)} MB`; r.appendChild(t); g.appendChild(r);
      const mx = ((node.x0 ?? 0) + (node.x1 ?? 0)) / 2, isL = mx < W / 2;
      const txt = ce("text", { x: isL ? (node.x1 ?? 0) + 4 : (node.x0 ?? 0) - 4, y: ((node.y0 ?? 0) + (node.y1 ?? 0)) / 2, dy: "0.35em", "text-anchor": isL ? "start" : "end", "font-size": "10", fill: "currentColor", class: "fill-foreground" });
      txt.textContent = (node.label || "").length > 24 ? (node.label || "").slice(0, 22) + "\u2026" : node.label || ""; g.appendChild(txt);
      svgEl.appendChild(g);
    }
  }, [data]);
  if (loading) return <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6"><div className="h-[400px] bg-muted rounded animate-pulse flex items-center justify-center"><p className="text-sm text-muted-foreground">Loading sankey...</p></div></div>;
  if (error) return <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6"><ErrorText /></div>;
  if (!data?.nodes?.length) return <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6"><EmptyState message="No sankey data" /></div>;
  return <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6"><div className="overflow-x-auto"><svg ref={svgRef} viewBox="0 0 800 400" className="w-full" style={{ minWidth: 500 }} /></div></div>;
}

function SkeletonBars({ count }: { count: number }) { return <div className="space-y-2 animate-pulse">{Array.from({ length: count }).map((_, i) => <div key={i} className="h-4 bg-muted rounded" style={{ width: `${100 - i * 15}%` }} />)}</div>; }
function SkeletonChart() { return <div className="h-48 bg-muted rounded animate-pulse" />; }
function ErrorText() { return <p className="text-xs text-destructive text-center py-6">Failed to load</p>; }

const RANK_COLORS: Record<string, { bg: string; bar: string; text: string }> = {
  blue: { bg: "bg-blue-50 dark:bg-blue-950/20", bar: "bg-blue-500", text: "text-blue-700 dark:text-blue-300" },
  emerald: { bg: "bg-emerald-50 dark:bg-emerald-950/20", bar: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-300" },
  cyan: { bg: "bg-cyan-50 dark:bg-cyan-950/20", bar: "bg-cyan-500", text: "text-cyan-700 dark:text-cyan-300" },
  violet: { bg: "bg-violet-50 dark:bg-violet-950/20", bar: "bg-violet-500", text: "text-violet-700 dark:text-violet-300" },
  pink: { bg: "bg-pink-50 dark:bg-pink-950/20", bar: "bg-pink-500", text: "text-pink-700 dark:text-pink-300" },
  orange: { bg: "bg-orange-50 dark:bg-orange-950/20", bar: "bg-orange-500", text: "text-orange-700 dark:text-orange-300" },
};

interface RankedItem { name: string; value: number; mono?: boolean; }

function RankedCard({ title, loading, error, items, color, wide }: { title: string; loading: boolean; error: boolean; items: RankedItem[]; color: string; wide?: boolean }) {
  const c = RANK_COLORS[color] || RANK_COLORS.blue;
  const maxVal = items.length > 0 ? items[0].value : 1;
  return <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-6"><h2 className="text-lg font-semibold mb-3">{title}</h2>
    {loading ? <SkeletonBars count={5} /> : error ? <ErrorText /> : items.length > 0 ? (
      <div className="space-y-1.5">{items.slice(0, 10).map((item, i) => { const pct = Math.max(2, (item.value / maxVal) * 100);
        return <div key={i} className="flex items-center gap-2 text-xs"><span className="w-5 text-right text-muted-foreground shrink-0">{i + 1}</span>
          <div className="flex-1 min-w-0"><div className="flex items-center justify-between mb-0.5"><span className={`truncate ${item.mono ? "font-mono text-[11px]" : ""}`} title={item.name}>{item.name}</span><span className="ml-2 text-muted-foreground shrink-0">{formatBytes(item.value)}</span></div>
            <div className={`h-1.5 rounded-full ${c.bg}`}><div className={`h-full rounded-full ${c.bar}`} style={{ width: `${pct}%`, opacity: 1 - i * 0.06 }} /></div></div></div>; })}</div>) : <EmptyState message={`No ${title.toLowerCase()} data`} />}</div>;
}

function EmptyState({ message }: { message?: string }) {
  return <div className="flex flex-col items-center justify-center py-8 text-muted-foreground"><svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 mb-2 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" /></svg><p className="text-xs text-center">{message || "No data available"}</p></div>;
}

function FlowRecordsTable({ records }: { records: TrafficInboundTableRecord[] }) {
  return <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="border-b text-muted-foreground text-left"><th className="py-2 pr-3 font-medium">Client IP</th><th className="py-2 pr-3 font-medium">Server IP</th><th className="py-2 pr-3 font-medium">Service</th><th className="py-2 pr-3 font-medium text-right">Bytes</th><th className="py-2 pr-3 font-medium text-right">Packets</th><th className="py-2 font-medium text-right">Sessions</th></tr></thead>
    <tbody>{records.map((r, i) => <tr key={i} className="border-b border-muted/50 last:border-0 hover:bg-muted/30 transition-colors"><td className="py-1.5 pr-3 font-mono text-[11px]">{r.client_ip}</td><td className="py-1.5 pr-3 font-mono text-[11px]">{r.server_ip}</td><td className="py-1.5 pr-3 truncate max-w-[150px]">{r.service}</td><td className="py-1.5 pr-3 text-right whitespace-nowrap">{formatBytes(r.bytes)}</td><td className="py-1.5 pr-3 text-right whitespace-nowrap">{r.packets.toLocaleString()}</td><td className="py-1.5 text-right whitespace-nowrap">{r.sessions.toLocaleString()}</td></tr>)}</tbody></table></div>;
}

function appColor(index: number, total: number): string { const hue = (index * 360) / total, sat = 70 + (index % 3) * 10, lit = 45 + (index % 4) * 8; return `hsl(${hue}, ${sat}%, ${lit}%)`; }
function formatStackTs(row: Record<string, any>): string { const ts = row.timestampMs || row.timestamp; if (!ts) return ""; const ms = typeof ts === "number" ? ts : new Date(ts).getTime(); if (isNaN(ms)) return String(ts).slice(-8) || ""; return new Date(ms).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Jakarta" }); }

function StackedBarChart({ data, serviceNames }: { data: Record<string, any>[]; serviceNames: string[] }) {
  const [hoveredBar, setHoveredBar] = useState<{ barIndex: number; sBreakdown: { svc: string; mbps: number; color: string }[]; x: number } | null>(null);
  if (!data.length || !serviceNames.length) return <EmptyState message="No service throughput data" />;
  const W = 800, H = 380, pad = { top: 10, right: 30, bottom: 50, left: 65 }, plotW = W - pad.left - pad.right, plotH = H - pad.top - pad.bottom, barGap = 2;
  let maxTotal = 0; for (const row of data) { let sum = 0; for (const svc of serviceNames) sum += Number(row[svc]) || 0; if (sum > maxTotal) maxTotal = sum; }
  if (maxTotal === 0) maxTotal = 1;
  const barWidth = Math.max(2, plotW / data.length - barGap), yTickValues = Array.from({ length: 6 }, (_, i) => (maxTotal / 5) * i);
  const colorMap: Record<string, string> = {}; serviceNames.forEach((svc, i) => { colorMap[svc] = appColor(i, serviceNames.length); });
  const xScale = (i: number) => pad.left + i * (plotW / data.length), yScale = (v: number) => pad.top + plotH - (v / maxTotal) * plotH, xLabelEvery = Math.max(1, Math.floor(data.length / 8));
  return <div className="relative">
    <div className="flex flex-wrap gap-x-4 gap-y-1 mb-3">{serviceNames.slice(0, 25).map((svc) => <div key={svc} className="flex items-center gap-1.5 text-[11px]"><span className="w-3 h-3 rounded-sm shrink-0" style={{ backgroundColor: colorMap[svc] }} /><span className="text-muted-foreground truncate max-w-[120px]">{svc}</span></div>)}{serviceNames.length > 25 && <span className="text-[11px] text-muted-foreground">+{serviceNames.length - 25} more</span>}</div>
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 420 }}>
      {yTickValues.map((v) => <g key={v}><line x1={pad.left} x2={W - pad.right} y1={yScale(v)} y2={yScale(v)} className="stroke-muted-foreground/15" strokeWidth={0.5} /><text x={pad.left - 6} y={yScale(v) + 4} textAnchor="end" className="text-[10px] fill-muted-foreground">{v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v >= 0.01 ? v.toFixed(2) : v.toFixed(4)}</text></g>)}
      {data.map((row, i) => { const x = xScale(i); let yOff = yScale(0); return <g key={i}>{serviceNames.map((svc) => { const mbps = Number(row[svc]) || 0; if (mbps <= 0) return null; const barH = (mbps / maxTotal) * plotH, actualY = yOff - barH; const el = <rect key={svc} x={x} y={actualY} width={Math.max(1, barWidth - barGap)} height={Math.max(1, barH)} fill={colorMap[svc]} className="cursor-pointer transition-opacity hover:opacity-80" onMouseEnter={(e) => { const bd = serviceNames.filter((s) => (Number(row[s]) || 0) > 0).map((s) => ({ svc: s, mbps: Number(row[s]) || 0, color: colorMap[s] })).sort((a, b) => b.mbps - a.mbps); const rect = (e.target as SVGRectElement).getBoundingClientRect(); setHoveredBar({ barIndex: i, sBreakdown: bd, x: rect.left + rect.width / 2 }); }} onMouseLeave={() => setHoveredBar(null)} />; yOff = actualY; return el; })}</g>; })}
      {data.map((row, i) => { if (i % xLabelEvery !== 0 && i !== data.length - 1) return null; return <text key={i} x={xScale(i) + barWidth / 2} y={H - pad.bottom + 16} textAnchor="middle" className="text-[9px] fill-muted-foreground">{formatStackTs(row)}</text>; })}
      <text x={12} y={H / 2} textAnchor="middle" className="text-[10px] fill-muted-foreground" transform={`rotate(-90, 12, ${H / 2})`}>Mbps</text>
    </svg>
    {hoveredBar && <div className="fixed z-50 bg-card border rounded-lg shadow-lg p-3 text-xs pointer-events-none" style={{ left: Math.min(hoveredBar.x, window.innerWidth - 260), top: 60, maxWidth: 250 }}>
      <p className="font-semibold mb-2 text-muted-foreground">{formatStackTs(data[hoveredBar.barIndex])}</p>
      {hoveredBar.sBreakdown.map(({ svc, mbps, color }) => <div key={svc} className="flex items-center justify-between gap-3 py-0.5"><div className="flex items-center gap-1.5 min-w-0"><span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: color }} /><span className="truncate">{svc}</span></div><span className="font-mono font-medium shrink-0">{mbps >= 100 ? `${mbps.toFixed(0)} Mbps` : mbps >= 1 ? `${mbps.toFixed(2)} Mbps` : mbps >= 0.01 ? `${mbps.toFixed(2)} Mbps` : `${mbps.toFixed(4)} Mbps`}</span></div>)}
      <div className="border-t mt-2 pt-1.5 flex justify-between font-semibold"><span>Total</span><span className="font-mono">{hoveredBar.sBreakdown.reduce((s, a) => s + a.mbps, 0).toFixed(2)} Mbps</span></div></div>}
  </div>;
}
