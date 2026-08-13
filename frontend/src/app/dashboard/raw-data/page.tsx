"use client";

import { useState, useMemo } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken, hasMinRole } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, formatBytes, getDefaultTimeRange } from "@/lib/constants";
import { TagFilterField, splitChips } from "@/components/TagFilterField";
import type { RawFlowRecord, APIResponse } from "@/types";

// ── Constants ────────────────────────────────────────────────────
const PAGE_SIZES = [25, 50, 100, 250, 500];
const MAX_RECORDS = 10_000; // Q-08: search_after cap

// ── Filter types (multi-value, consistent with Traffic* pages) ───
interface FilterState {
  client_ip: string[];
  server_ip: string[];
  application: string[];
  category: string[];
  protocol: string[];
  dst_port: string[];
  ingress_interface: string[];
  egress_interface: string[];
  correlation_id: string[];
}

const defaultFilters: FilterState = {
  client_ip: [],
  server_ip: [],
  application: [],
  category: [],
  protocol: [],
  dst_port: [],
  ingress_interface: [],
  egress_interface: [],
  correlation_id: [],
};

function countActiveFilters(f: FilterState): number {
  return Object.values(f).reduce((n, arr) => n + arr.length, 0);
}

// ── Component ────────────────────────────────────────────────────
export default function RawDataPage() {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [pageSize, setPageSize] = useState(25);
  const [filters, setFilters] = useState<FilterState>(defaultFilters);
  const [draftFilters, setDraftFilters] = useState<FilterState>(defaultFilters);
  const [showFilters, setShowFilters] = useState(false);
  const [siteName, setSiteName] = useState("Site_FGT-DC");
  const [pathFilter, setPathFilter] = useState("internet");
  const [direction, setDirection] = useState(""); // upload, download

  // ── Pagination state (search_after cursor stack) ──────────────
  const [cursorStack, setCursorStack] = useState<(number | string)[][]>([]);
  // currentCursor is the search_after array from the PREVIOUS page's response
  const [currentCursor, setCurrentCursor] = useState<(number | string)[] | null>(null);
  const [pageIndex, setPageIndex] = useState(0);

  // ── Column visibility ─────────────────────────────────────────
  const [visibleColumns, setVisibleColumns] = useState({
    timestamp: true, client_ip: true, server_ip: true, application: true,
    category: true, classification: true, protocol: true, dst_port: true, total_bytes: true,
    packets: true, ingress_interface: true, egress_interface: true,
    correlation_id: true, correlation_direction: true, path: false,
    risk: true, vendor: true, tech: true, url: true,
  });

  // ── Build query params ────────────────────────────────────────
  const queryParams = useMemo(() => {
    const p: Record<string, string | number | undefined> = {
      gte_ms: gteMs,
      lte_ms: lteMs,
      page_size: pageSize,
      sort_by: "@timestamp",
      sort_dir: "desc",
      site_name: siteName,
      path_filter: pathFilter,
    };
    if (direction) p.direction = direction;
    // search_after cursor from previous response
    if (currentCursor && currentCursor.length >= 2) {
      p.search_after_timestamp = currentCursor[0];
      p.search_after_id = String(currentCursor[1]);
    }
    // Split each field's chips into include / exclude (leading "-") → <param> / <param>_not.
    const push = (param: string, vals: string[]) => {
      const { include, exclude } = splitChips(vals);
      if (include.length) p[param] = include.join(",");
      if (exclude.length) p[param + "_not"] = exclude.join(",");
    };
    push("client_ip", filters.client_ip);
    push("server_ip", filters.server_ip);
    push("application", filters.application);
    push("category", filters.category);
    push("protocol", filters.protocol);
    push("dst_port", filters.dst_port);
    push("ingress_interface", filters.ingress_interface);
    push("egress_interface", filters.egress_interface);
    push("correlation_id", filters.correlation_id);
    return p;
  }, [gteMs, lteMs, pageSize, currentCursor, filters, siteName, pathFilter, direction]);

  // ── SWR fetch ─────────────────────────────────────────────────
  const token = typeof window !== "undefined" ? getAccessToken() : null;
  const canFetch = token !== null && hasMinRole("operator");

  const { data, error, isLoading } = useSWR<APIResponse<RawFlowRecord[]>>(
    canFetch
      ? `/api/v1/traffic/raw?${new URLSearchParams(
          Object.entries(queryParams)
            .filter(([, v]) => v !== undefined && v !== null)
            .map(([k, v]) => [k, String(v)]) as [string, string][]
        ).toString()}`
      : null,
    swrFetcher,
    { refreshInterval: autoRefresh ? 60_000 : 0 }
  );

  const isTimeout = (error as any)?.code === "TIMEOUT";
  const records = data?.data || [];
  const total = data?.meta?.total || 0;
  const sessions = data?.meta?.total_sessions || 0;
  const queryTook = data?.meta?.query_took_ms;
  // The search_after cursor returned by the API (includes _id tiebreaker)
  const nextCursor = (data?.meta?.search_after as (number | string)[] | null) || null;

  // ── Privilege guard ───────────────────────────────────────────
  if (!hasMinRole("operator")) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">Raw Data</h1>
        <div className="bg-card border rounded-lg p-8 text-center space-y-2">
          <p className="text-base font-semibold">Operator access required</p>
          <p className="text-sm text-muted-foreground max-w-md mx-auto">
            Raw flow data is available to <span className="font-mono">operator</span> and
            above. Contact a superadmin if you need elevated access.
          </p>
        </div>
      </div>
    );
  }

  // ── Handlers ──────────────────────────────────────────────────
  function handlePreset(seconds: number, label: string) {
    const now = Date.now();
    setGteMs(now - seconds * 1000);
    setLteMs(now);
    setSelectedPreset(label);
    setAutoRefresh(true);
    resetPagination();
  }

  function applyFilters() {
    setFilters({ ...draftFilters });
    resetPagination();
  }

  function clearFilters() {
    setDraftFilters(defaultFilters);
    setFilters(defaultFilters);
    resetPagination();
  }

  function resetPagination() {
    setCurrentCursor(null);
    setCursorStack([]);
    setPageIndex(0);
  }

  function nextPage() {
    // Q-08: enforce 10k cap
    const recordsScanned = pageIndex * pageSize + records.length;
    if (records.length < pageSize || !nextCursor || recordsScanned >= MAX_RECORDS) return;
    setCursorStack([...cursorStack, currentCursor || []]);
    setCurrentCursor(nextCursor);
    setPageIndex(pageIndex + 1);
  }

  function prevPage() {
    if (cursorStack.length === 0) return;
    const prev = cursorStack[cursorStack.length - 1];
    setCursorStack(cursorStack.slice(0, -1));
    setCurrentCursor(prev.length > 0 ? prev : null);
    setPageIndex(Math.max(0, pageIndex - 1));
  }

  // ── Column definitions ────────────────────────────────────────
  const columns = [
    { key: "timestamp", label: "Timestamp", visible: visibleColumns.timestamp, sortable: false },
    { key: "client_ip", label: "Client IP", visible: visibleColumns.client_ip, sortable: false },
    { key: "server_ip", label: "Server IP", visible: visibleColumns.server_ip, sortable: false },
    { key: "application", label: "Application", visible: visibleColumns.application, sortable: false },
    { key: "category", label: "Category", visible: visibleColumns.category, sortable: false },
    { key: "risk", label: "Risk", visible: visibleColumns.risk, sortable: false },
    { key: "vendor", label: "Vendor", visible: visibleColumns.vendor, sortable: false },
    { key: "tech", label: "Technology", visible: visibleColumns.tech, sortable: false },
    { key: "url", label: "App URL", visible: visibleColumns.url, sortable: false },
    { key: "classification", label: "Classification", visible: visibleColumns.classification, sortable: false },
    { key: "protocol", label: "Protocol", visible: visibleColumns.protocol, sortable: false },
    { key: "dst_port", label: "Dst Port", visible: visibleColumns.dst_port, sortable: false },
    { key: "total_bytes", label: "Total Bytes", visible: visibleColumns.total_bytes, sortable: false },
    { key: "packets", label: "Packets", visible: visibleColumns.packets, sortable: false },
    { key: "ingress_interface", label: "Ingress Interface", visible: visibleColumns.ingress_interface, sortable: false },
    { key: "egress_interface", label: "Egress Interface", visible: visibleColumns.egress_interface, sortable: false },
    { key: "correlation_id", label: "Correlation ID", visible: visibleColumns.correlation_id, sortable: false },
    { key: "correlation_direction", label: "Direction", visible: visibleColumns.correlation_direction, sortable: false },
    { key: "path", label: "Traffic Path", visible: visibleColumns.path, sortable: false },
  ];

  const visibleCols = columns.filter((c) => c.visible);

  // ── CSV export ────────────────────────────────────────────────
  function exportCSV() {
    if (records.length === 0) return;
    const headers = visibleCols.map((c) => c.label).join(",");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rows = (records as any[]).map((r: any) =>
      visibleCols.map((c) => {
        const val = r[c.key];
        if (val === null || val === undefined) return "";
        const str = String(val);
        return str.includes(",") ? `"${str}"` : str;
      }).join(",")
    );
    const csv = [headers, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `nod_raw_flows_${new Date().toISOString().slice(0, 19)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── Pagination controls ───────────────────────────────────────
  const recordsScanned = pageIndex * pageSize + records.length;
  const hasMore = records.length >= pageSize && nextCursor && recordsScanned < MAX_RECORDS;
  const hasPrev = cursorStack.length > 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Raw Flow Data</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={siteName}
            onChange={(e) => { setSiteName(e.target.value); resetPagination(); }}
            className="px-3 py-1.5 rounded-md border border-border/60 bg-background text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="Site_FGT-DC">Site_FGT-DC</option>
            <option value="Site_FGT-DRC">Site_FGT-DRC</option>
            <option value="Site_FGT_Office">Site_FGT_Office</option>
          </select>
          <select
            value={pathFilter}
            onChange={(e) => { setPathFilter(e.target.value); resetPagination(); }}
            className="px-3 py-1.5 rounded-md border border-border/60 bg-background text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
            title="Traffic path"
          >
            <option value="internet">Internet</option>
            <option value="inbound-vip">Inbound-vip</option>
            <option value="inter-site">Inter-site</option>
            <option value="intra-lan">Intra-lan</option>
          </select>
          <div className="flex gap-1 bg-muted/40 dark:bg-muted/30 rounded-md p-1">
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
          </div>
          <span className="text-xs text-muted-foreground">
            {queryTook != null ? `${queryTook}ms` : ""}
          </span>
        </div>
      </div>

      {/* Error / timeout banner */}
      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
          {isTimeout
            ? "Request timed out. Please retry or reduce the time range."
            : "Failed to load raw data."}
          <button onClick={() => window.location.reload()} className="underline hover:no-underline transition-all ml-2">Retry</button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center justify-between flex-wrap gap-3 bg-card border rounded-lg p-3">
        <div className="flex items-center gap-3">
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); resetPagination(); }}
            className="px-2 py-1 text-xs rounded border bg-background"
          >
            {PAGE_SIZES.map((s) => (
              <option key={s} value={s}>{s} rows</option>
            ))}
          </select>
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={cn(
              "px-2.5 py-1 text-xs rounded-md border transition-colors",
              showFilters ? "bg-primary text-primary-foreground border-primary" : "bg-background text-muted-foreground border-border"
            )}
          >
            Filters {countActiveFilters(filters) > 0 ? `(${countActiveFilters(filters)})` : ""}
          </button>
          <button onClick={exportCSV} className="px-2.5 py-1 text-xs rounded-md border bg-background text-muted-foreground border-border hover:bg-muted">
            Export CSV
          </button>
          {/* Column toggle */}
          <div className="relative group">
            <button className="px-2.5 py-1 text-xs rounded-md border bg-background text-muted-foreground border-border hover:bg-muted">
              Columns ▾
            </button>
            <div className="absolute left-0 top-full mt-1 bg-card border rounded-lg shadow-lg p-3 hidden group-hover:block z-50 w-48">
              {columns.map((col) => (
                <label key={col.key} className="flex items-center gap-2 text-xs py-1 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={col.visible}
                    onChange={() => setVisibleColumns({ ...visibleColumns, [col.key]: !col.visible })}
                    className="rounded"
                  />
                  {col.label}
                </label>
              ))}
            </div>
          </div>
        </div>
        <span className="text-xs text-muted-foreground">
          {total > 0
            ? `~${total.toLocaleString()} total records${
                sessions > 0 ? ` · ~${sessions.toLocaleString()} sessions` : ""
              }`
            : ""}
        </span>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <TagFilterField label="Client IP" values={draftFilters.client_ip}
              onChange={(v) => setDraftFilters({ ...draftFilters, client_ip: v })} mono
              placeholder="e.g. 10.0.0.5" />
            <TagFilterField label="Server IP" values={draftFilters.server_ip}
              onChange={(v) => setDraftFilters({ ...draftFilters, server_ip: v })} mono
              placeholder="e.g. 8.8.8.8" />
            <TagFilterField label="Application" values={draftFilters.application}
              onChange={(v) => setDraftFilters({ ...draftFilters, application: v })}
              placeholder="e.g. HTTPS" />
            <TagFilterField label="Category" values={draftFilters.category}
              onChange={(v) => setDraftFilters({ ...draftFilters, category: v })}
              placeholder="e.g. Web.Client" />
            <TagFilterField label="Protocol" values={draftFilters.protocol}
              onChange={(v) => setDraftFilters({ ...draftFilters, protocol: v })}
              placeholder="e.g. tcp" />
            <TagFilterField label="Dst Port" values={draftFilters.dst_port}
              onChange={(v) => setDraftFilters({ ...draftFilters, dst_port: v })}
              placeholder="e.g. 443" />
            <TagFilterField label="Ingress Interface" values={draftFilters.ingress_interface}
              onChange={(v) => setDraftFilters({ ...draftFilters, ingress_interface: v })}
              placeholder="e.g. port1" />
            <TagFilterField label="Egress Interface" values={draftFilters.egress_interface}
              onChange={(v) => setDraftFilters({ ...draftFilters, egress_interface: v })}
              placeholder="e.g. wan1" />
            <TagFilterField label="Correlation ID" values={draftFilters.correlation_id}
              onChange={(v) => setDraftFilters({ ...draftFilters, correlation_id: v })} mono
              placeholder="e.g. a1b2c3d4" />
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={applyFilters} className="px-3 py-1 text-xs rounded-md bg-primary text-primary-foreground">
              Apply Filters
            </button>
            <button onClick={clearFilters} className="px-3 py-1 text-xs rounded-md border border-border text-muted-foreground">
              Clear All
            </button>
          </div>
        </div>
      )}

      {/* Data Table */}
      <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-muted/50">
                {visibleCols.map((col) => (
                  <th
                    key={col.key}
                    className="text-left py-2.5 px-3 font-medium text-muted-foreground whitespace-nowrap"
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: Math.min(pageSize, 50) }).map((_, i) => (
                  <tr key={i} className="border-b last:border-0 animate-pulse">
                    {visibleCols.map((col) => (
                      <td key={col.key} className="py-3 px-3">
                        <div className="h-3 bg-muted rounded w-full" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : records.length === 0 ? (
                <tr>
                  <td colSpan={visibleCols.length} className="py-12 text-center text-muted-foreground">
                    No data can be see, because not have data on index.
                  </td>
                </tr>
              ) : (
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                (records as any[]).map((row: any, i: number) => (
                  <tr key={i} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                    {visibleCols.map((col) => {
                      const val = row[col.key];
                      if (col.key === "timestamp") {
                        return (
                          <td key={col.key} className="py-2 px-3 font-mono whitespace-nowrap">
                            {new Date(val as string).toLocaleString()}
                          </td>
                        );
                      }
                      if (col.key === "total_bytes") {
                        return (
                          <td key={col.key} className="py-2 px-3 text-right font-mono whitespace-nowrap">
                            {row.bytes_human || formatBytes(val as number)}
                          </td>
                        );
                      }
                      if (col.key === "packets") {
                        return (
                          <td key={col.key} className="py-2 px-3 text-right font-mono">
                            {Number(val).toLocaleString()}
                          </td>
                        );
                      }
                      if (col.key === "dst_port") {
                        return (
                          <td key={col.key} className="py-2 px-3 font-mono text-center">
                            {val}
                          </td>
                        );
                      }
                      if (col.key === "correlation_direction") {
                        const dir = String(val || "");
                        return (
                          <td key={col.key} className="py-2 px-3 text-center">
                            <span className={cn(
                              "px-1.5 py-0.5 rounded text-[10px] font-medium",
                              dir === "initiator" ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400" :
                              dir === "responder" ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400" :
                              "bg-muted text-muted-foreground"
                            )}>
                              {dir || "—"}
                            </span>
                          </td>
                        );
                      }
                      if (col.key === "path") {
                        const p = String(val || "");
                        return (
                          <td key={col.key} className="py-2 px-3 text-center">
                            <span className={cn(
                              "px-1.5 py-0.5 rounded text-[10px] font-medium",
                              p === "internet" ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400" :
                              p === "inbound-vip" ? "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400" :
                              p === "inter-site" ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400" :
                              p === "intra-lan" ? "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400" :
                              "bg-muted text-muted-foreground"
                            )}>
                              {p || "—"}
                            </span>
                          </td>
                        );
                      }
                      if (col.key === "correlation_id") {
                        return (
                          <td key={col.key} className="py-2 px-3 font-mono text-[11px]">
                            <span className="break-all" title={String(val || "")}>
                              {String(val || "—")}
                            </span>
                          </td>
                        );
                      }
                      if (col.key === "url") {
                        const u = String(val || "");
                        return (
                          <td key={col.key} className="py-2 px-3 whitespace-nowrap">
                            {u ? (
                              <a
                                href={u}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-600 dark:text-blue-400 hover:underline"
                                title={u}
                              >
                                check via link
                              </a>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                        );
                      }
                      return (
                        <td key={col.key} className="py-2 px-3 font-mono text-[11px] whitespace-nowrap truncate max-w-[150px]" title={String(val || "")}>
                          {String(val || "—")}
                        </td>
                      );
                    })}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between p-3 border-t bg-muted/20">
          <div className="flex items-center gap-2">
            <button
              onClick={prevPage}
              disabled={!hasPrev}
              className="px-2 py-1 text-xs rounded border bg-background disabled:opacity-30 disabled:cursor-not-allowed hover:bg-muted"
            >
              ← Previous
            </button>
            <span className="text-xs text-muted-foreground">Page {pageIndex + 1}</span>
            <button
              onClick={nextPage}
              disabled={!hasMore}
              className="px-2 py-1 text-xs rounded border bg-background disabled:opacity-30 disabled:cursor-not-allowed hover:bg-muted"
            >
              Next →
            </button>
          </div>
          <span className="text-[10px] text-muted-foreground">
            {records.length > 0
              ? `Showing ${recordsScanned - records.length + 1}–${recordsScanned} of ~${total.toLocaleString()}`
              : "No records"}
            {recordsScanned >= MAX_RECORDS && (
              <span className="ml-2 text-amber-600">(10k limit reached)</span>
            )}
          </span>
        </div>
      </div>
    </div>
  );
}
