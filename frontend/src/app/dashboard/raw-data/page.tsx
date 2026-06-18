"use client";

import { useState, useMemo, useCallback } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, formatBytes, getDefaultTimeRange } from "@/lib/constants";
import type { RawFlowRecord, APIResponse } from "@/types";

const PAGE_SIZES = [25, 50, 100];

interface FilterState {
  client_ip: string;
  server_ip: string;
  application: string;
  category: string;
  protocol: string;
  dst_port: string;
  ingress_zone: string;
  egress_link: string;
}

const defaultFilters: FilterState = {
  client_ip: "",
  server_ip: "",
  application: "",
  category: "",
  protocol: "",
  dst_port: "",
  ingress_zone: "",
  egress_link: "",
};

interface SortState {
  column: string;
  dir: "asc" | "desc";
}

export default function RawDataPage() {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [autoRefresh, setAutoRefresh] = useState(false); // default OFF for raw data
  const [pageSize, setPageSize] = useState(25);
  const [filters, setFilters] = useState<FilterState>(defaultFilters);
  const [draftFilters, setDraftFilters] = useState<FilterState>(defaultFilters);
  const [sort, setSort] = useState<SortState>({ column: "@timestamp", dir: "desc" });
  const [showFilters, setShowFilters] = useState(false);
  const [cursorStack, setCursorStack] = useState<Array<[number, string]>>([]);
  const [currentCursor, setCurrentCursor] = useState<[number, string] | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [visibleColumns, setVisibleColumns] = useState({
    timestamp: true, client_ip: true, server_ip: true, application: true,
    category: true, protocol: true, dst_port: true, total_bytes: true,
    packets: true, ingress_zone: true, egress_link: true,
    correlation_id: true, correlation_direction: true,
  });
  const [siteName, setSiteName] = useState("Site_FGT_Office");

  // Build query params
  const queryParams = useMemo(() => {
    const p: Record<string, string | number | undefined> = {
      gte_ms: gteMs,
      lte_ms: lteMs,
      page_size: pageSize,
      sort_by: sort.column,
      sort_dir: sort.dir,
      site_name: siteName,
    };
    if (currentCursor) {
      p.search_after_timestamp = currentCursor[0];
      p.search_after_id = currentCursor[1];
    }
    if (filters.client_ip) p.client_ip = filters.client_ip;
    if (filters.server_ip) p.server_ip = filters.server_ip;
    if (filters.application) p.application = filters.application;
    if (filters.category) p.category = filters.category;
    if (filters.protocol) p.protocol = filters.protocol;
    if (filters.dst_port) p.dst_port = parseInt(filters.dst_port);
    if (filters.ingress_zone) p.ingress_zone = filters.ingress_zone;
    if (filters.egress_link) p.egress_link = filters.egress_link;
    return p;
  }, [gteMs, lteMs, pageSize, sort, currentCursor, filters, siteName]);

  const token = typeof window !== "undefined" ? getAccessToken() : null;
  const { data, error, isLoading } = useSWR<APIResponse<RawFlowRecord[]>>(
    token ? `/api/v1/traffic/raw?${new URLSearchParams(
      Object.entries(queryParams)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => [k, String(v)])
    ).toString()}` : null,
    swrFetcher,
    { refreshInterval: autoRefresh ? 60000 : 0 }
  );

  const records = data?.data || [];
  const total = data?.meta?.total || 0;
  const queryTook = data?.meta?.query_took_ms;

  function handlePreset(seconds: number, label: string) {
    const now = Date.now();
    setGteMs(now - seconds * 1000);
    setLteMs(now);
    setSelectedPreset(label);
    setAutoRefresh(true);
  }

  function applyFilters() {
    setFilters({ ...draftFilters });
    setCurrentCursor(null);
    setCursorStack([]);
    setPageIndex(0);
  }

  function clearFilters() {
    setDraftFilters(defaultFilters);
    setFilters(defaultFilters);
    setCurrentCursor(null);
    setCursorStack([]);
    setPageIndex(0);
  }

  function nextPage() {
    if (records.length < pageSize) return; // no more pages
    const last = records[records.length - 1];
    const newCursor: [number, string] = [
      new Date(last.timestamp).getTime(),
      "", // _id not exposed in API response; will use timestamp only
    ];
    setCursorStack([...cursorStack, currentCursor || [0, ""]]);
    setCurrentCursor(newCursor);
    setPageIndex(pageIndex + 1);
  }

  function prevPage() {
    if (cursorStack.length === 0) return;
    const prev = cursorStack[cursorStack.length - 1];
    setCursorStack(cursorStack.slice(0, -1));
    setCurrentCursor(prev[0] !== 0 ? prev : null);
    setPageIndex(Math.max(0, pageIndex - 1));
  }

  function toggleSort(column: string) {
    if (sort.column === column) {
      setSort({ column, dir: sort.dir === "asc" ? "desc" : "asc" });
    } else {
      setSort({ column, dir: "desc" });
    }
    setCurrentCursor(null);
    setCursorStack([]);
    setPageIndex(0);
  }

  // Column definitions
  const columns = [
    { key: "timestamp", label: "Timestamp", visible: visibleColumns.timestamp, sortable: true },
    { key: "client_ip", label: "Client IP", visible: visibleColumns.client_ip, sortable: true },
    { key: "server_ip", label: "Server IP", visible: visibleColumns.server_ip, sortable: true },
    { key: "application", label: "Application", visible: visibleColumns.application, sortable: true },
    { key: "category", label: "Category", visible: visibleColumns.category, sortable: true },
    { key: "protocol", label: "Protocol", visible: visibleColumns.protocol, sortable: false },
    { key: "dst_port", label: "Dst Port", visible: visibleColumns.dst_port, sortable: true },
    { key: "total_bytes", label: "Total Bytes", visible: visibleColumns.total_bytes, sortable: true },
    { key: "packets", label: "Packets", visible: visibleColumns.packets, sortable: true },
    { key: "ingress_zone", label: "Ingress Zone", visible: visibleColumns.ingress_zone, sortable: false },
    { key: "egress_link", label: "Egress Link", visible: visibleColumns.egress_link, sortable: false },
    { key: "correlation_id", label: "Correlation ID", visible: visibleColumns.correlation_id, sortable: false },
    { key: "correlation_direction", label: "Direction", visible: visibleColumns.correlation_direction, sortable: false },
  ];

  const visibleCols = columns.filter((c) => c.visible);

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

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Raw Flow Data</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={siteName}
            onChange={(e) => {
              setSiteName(e.target.value);
              setCurrentCursor(null);
              setCursorStack([]);
              setPageIndex(0);
            }}
            className="px-3 py-1.5 rounded-md border border-border/60 bg-background text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="Site_FGT_Office">Site_FGT_Office</option>
            <option value="Site_FGT-DC">Site_FGT-DC</option>
            <option value="Site_FGT-DRC">Site_FGT-DRC</option>
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

      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
          Failed to load raw data.{" "}
          <button onClick={() => window.location.reload()} className="underline hover:no-underline transition-all">Retry</button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center justify-between flex-wrap gap-3 bg-card border rounded-lg p-3">
        <div className="flex items-center gap-3">
          <select
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setCurrentCursor(null);
              setCursorStack([]);
              setPageIndex(0);
            }}
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
            Filters {Object.values(filters).some((v) => v) ? "●" : ""}
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
          {total > 0 ? `~${total.toLocaleString()} total records` : ""}
        </span>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="bg-card border border-border/60 dark:border-border/40 rounded-lg shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20 p-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { key: "client_ip", label: "Client IP" },
              { key: "server_ip", label: "Server IP" },
              { key: "application", label: "Application" },
              { key: "category", label: "Category" },
              { key: "protocol", label: "Protocol" },
              { key: "dst_port", label: "Dst Port" },
              { key: "ingress_zone", label: "Ingress Zone" },
              { key: "egress_link", label: "Egress Link" },
            ].map((f) => (
              <div key={f.key}>
                <label className="text-[10px] text-muted-foreground uppercase tracking-wider">{f.label}</label>
                <input
                  type={f.key === "dst_port" ? "number" : "text"}
                  value={(draftFilters as unknown as Record<string, string>)[f.key] || ""}
                  onChange={(e) =>
                    setDraftFilters({ ...draftFilters, [f.key]: e.target.value })
                  }
                  placeholder={f.label}
                  className="w-full px-2 py-1 text-xs rounded border bg-background mt-1"
                />
              </div>
            ))}
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
                    className={cn(
                      "text-left py-2.5 px-3 font-medium text-muted-foreground whitespace-nowrap",
                      col.sortable && "cursor-pointer hover:text-foreground select-none"
                    )}
                    onClick={() => col.sortable && toggleSort(col.key)}
                  >
                    <span className="flex items-center gap-1">
                      {col.label}
                      {col.sortable && sort.column === col.key && (
                        <span className="text-primary">{sort.dir === "asc" ? "↑" : "↓"}</span>
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: pageSize }).map((_, i) => (
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
                    No records found for the selected range and filters.
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
                      if (col.key === "correlation_id") {
                        return (
                          <td key={col.key} className="py-2 px-3 font-mono text-[10px] truncate max-w-[180px]" title={String(val || "—")}>
                            {val ? String(val).slice(0, 20) + "…" : "—"}
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
              disabled={cursorStack.length === 0}
              className="px-2 py-1 text-xs rounded border bg-background disabled:opacity-30 disabled:cursor-not-allowed hover:bg-muted"
            >
              ← Previous
            </button>
            <span className="text-xs text-muted-foreground">Page {pageIndex + 1}</span>
            <button
              onClick={nextPage}
              disabled={records.length < pageSize}
              className="px-2 py-1 text-xs rounded border bg-background disabled:opacity-30 disabled:cursor-not-allowed hover:bg-muted"
            >
              Next →
            </button>
          </div>
          <span className="text-[10px] text-muted-foreground">
            Showing {records.length} of ~{total.toLocaleString()} records
          </span>
        </div>
      </div>
    </div>
  );
}
