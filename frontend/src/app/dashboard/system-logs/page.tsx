"use client";

import { useState, useMemo, Fragment } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";

type Level = "INFO" | "ALERT" | "ERROR" | "WARNING";
const LEVELS: Level[] = ["INFO", "ALERT", "ERROR", "WARNING"];
const CATEGORIES = ["auth", "api", "alert", "notify", "query", "health", "report", "frontend", "system"];

const LEVEL_BADGE: Record<Level, string> = {
  INFO: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  ALERT: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  ERROR: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  WARNING: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
};
const LEVEL_DOT: Record<Level, string> = {
  INFO: "bg-slate-400", ALERT: "bg-blue-500", ERROR: "bg-red-500", WARNING: "bg-amber-500",
};

interface SystemLogEntry {
  id: string;
  ts: string;
  level: Level;
  category: string;
  source: "backend" | "frontend";
  event: string;
  message: string;
  username: string | null;
  user_id: string | null;
  source_ip: string | null;
  trace_id: string | null;
  rule_id: string | null;
  method: string | null;
  path: string | null;
  status_code: number | null;
  duration_ms: number | null;
  details: Record<string, any> | null;
}

interface SystemLogResponse {
  data: { items: SystemLogEntry[]; total: number; facets: { level: Record<string, number> } };
}

const RANGES: Record<string, number | null> = {
  "Last 1h": 3600_000,
  "Last 24h": 86_400_000,
  "Last 7d": 604_800_000,
  "All time": null,
};
const LIMIT = 50;

export default function SystemLogsPage() {
  const token = typeof window !== "undefined" ? getAccessToken() : null;

  const [levels, setLevels] = useState<Set<Level>>(new Set());
  const [source, setSource] = useState("all");
  const [category, setCategory] = useState("all");
  const [username, setUsername] = useState("");
  const [search, setSearch] = useState("");
  const [range, setRange] = useState("Last 24h");
  const [offset, setOffset] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  // Any filter change resets pagination to the first page.
  const resetPage = () => setOffset(0);

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (levels.size) p.set("level", [...levels].join(","));
    if (source !== "all") p.set("source", source);
    if (category !== "all") p.set("category", category);
    if (username.trim()) p.set("username", username.trim());
    if (search.trim()) p.set("q", search.trim());
    const ms = RANGES[range];
    if (ms) p.set("from", new Date(Date.now() - ms).toISOString());
    p.set("limit", String(LIMIT));
    p.set("offset", String(offset));
    return p.toString();
  }, [levels, source, category, username, search, range, offset]);

  const swrKey = token ? `/api/v1/logs/system?${query}` : null;
  const { data, error, isLoading } = useSWR<SystemLogResponse>(swrKey, swrFetcher, {
    refreshInterval: autoRefresh ? 15000 : 0,
    keepPreviousData: true,
  });

  const items = data?.data?.items || [];
  const total = data?.data?.total || 0;
  const facets = data?.data?.facets?.level || {};

  const toggleLevel = (lv: Level) => {
    setLevels((prev) => {
      const next = new Set(prev);
      next.has(lv) ? next.delete(lv) : next.add(lv);
      return next;
    });
    resetPage();
  };

  const onExport = async () => {
    if (!token) return;
    const p = new URLSearchParams(query);
    p.delete("limit"); p.delete("offset");
    const resp = await fetch(`/api/v1/logs/system/export?${p.toString()}`, {
      headers: { Authorization: `Bearer ${token}` }, credentials: "include",
    });
    if (!resp.ok) return;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "system_logs.csv"; a.click();
    URL.revokeObjectURL(url);
  };

  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">System Logs</h1>
        <div className="p-6 bg-destructive/10 border border-destructive/20 rounded-lg text-center">
          <p className="text-destructive font-medium">Access Denied</p>
          <p className="text-sm text-muted-foreground mt-1">
            Only admin and superadmin can view system logs.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">System Logs</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            Live (15s)
          </label>
          <button onClick={onExport}
            className="px-3 py-1.5 text-xs font-medium border rounded-md hover:bg-muted transition-colors">
            Export CSV
          </button>
        </div>
      </div>

      {/* Level tabs (multi-select) */}
      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={() => { setLevels(new Set()); resetPage(); }}
          className={cn("px-3 py-1.5 rounded-md text-sm font-medium border transition-colors",
            levels.size === 0 ? "bg-primary text-primary-foreground border-primary" : "hover:bg-muted")}>
          All
        </button>
        {LEVELS.map((lv) => (
          <button key={lv} onClick={() => toggleLevel(lv)}
            className={cn("px-3 py-1.5 rounded-md text-sm font-medium border transition-colors flex items-center gap-2",
              levels.has(lv) ? "border-primary ring-1 ring-primary" : "hover:bg-muted")}>
            <span className={cn("h-2 w-2 rounded-full", LEVEL_DOT[lv])} />
            {lv}
            <span className="text-xs text-muted-foreground">{facets[lv] ?? 0}</span>
          </button>
        ))}
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <select value={source} onChange={(e) => { setSource(e.target.value); resetPage(); }}
          className="px-2.5 py-1.5 text-sm border rounded-md bg-background">
          <option value="all">All sources</option>
          <option value="backend">Backend</option>
          <option value="frontend">Frontend</option>
        </select>
        <select value={category} onChange={(e) => { setCategory(e.target.value); resetPage(); }}
          className="px-2.5 py-1.5 text-sm border rounded-md bg-background">
          <option value="all">All categories</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={range} onChange={(e) => { setRange(e.target.value); resetPage(); }}
          className="px-2.5 py-1.5 text-sm border rounded-md bg-background">
          {Object.keys(RANGES).map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <input type="text" value={username} onChange={(e) => { setUsername(e.target.value); resetPage(); }}
          placeholder="username…"
          className="px-3 py-1.5 text-sm border rounded-md bg-background w-36 focus:outline-none focus:ring-1 focus:ring-primary" />
        <input type="text" value={search} onChange={(e) => { setSearch(e.target.value); resetPage(); }}
          placeholder="search message / event / path…"
          className="px-3 py-1.5 text-sm border rounded-md bg-background w-64 focus:outline-none focus:ring-1 focus:ring-primary" />
        <span className="text-xs text-muted-foreground ml-auto">{total} entries</span>
      </div>

      {/* Table */}
      <div className="bg-card border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-muted-foreground">
                <th className="text-left py-2.5 px-3 w-40">Time (WIB)</th>
                <th className="text-left py-2.5 px-3 w-24">Level</th>
                <th className="text-left py-2.5 px-3 w-20">Source</th>
                <th className="text-left py-2.5 px-3 w-48">Event</th>
                <th className="text-left py-2.5 px-3">Message</th>
                <th className="text-left py-2.5 px-3 w-28">User</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && items.length === 0 ? (
                Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="border-b animate-pulse">
                    {[1, 2, 3, 4, 5, 6].map((j) => (
                      <td key={j} className="py-3 px-3"><div className="h-4 bg-muted rounded" /></td>
                    ))}
                  </tr>
                ))
              ) : items.length === 0 ? (
                <tr><td colSpan={6} className="py-10 text-center text-muted-foreground">No logs match these filters</td></tr>
              ) : (
                items.map((log) => (
                  <Fragment key={log.id}>
                    <tr
                      onClick={() => setExpanded(expanded === log.id ? null : log.id)}
                      className="border-b last:border-0 hover:bg-muted/30 transition-colors cursor-pointer">
                      <td className="py-2.5 px-3 text-xs font-mono whitespace-nowrap">
                        {new Date(log.ts).toLocaleString("en-GB", { timeZone: "Asia/Jakarta" })}
                      </td>
                      <td className="py-2.5 px-3">
                        <span className={cn("px-2 py-0.5 rounded text-[11px] font-medium", LEVEL_BADGE[log.level])}>
                          {log.level}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-xs capitalize text-muted-foreground">{log.source}</td>
                      <td className="py-2.5 px-3 text-xs font-mono">{log.event}</td>
                      <td className="py-2.5 px-3 text-xs max-w-md truncate" title={log.message}>{log.message}</td>
                      <td className="py-2.5 px-3 text-xs font-medium">{log.username || "—"}</td>
                    </tr>
                    {expanded === log.id && (
                      <tr className="border-b bg-muted/20">
                        <td colSpan={6} className="py-3 px-4">
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1 text-xs">
                            <div><span className="text-muted-foreground">category</span>: {log.category}</div>
                            <div><span className="text-muted-foreground">source IP</span>: {log.source_ip || "—"}</div>
                            <div className="truncate"><span className="text-muted-foreground">trace_id</span>: {log.trace_id || "—"}</div>
                            <div><span className="text-muted-foreground">rule_id</span>: {log.rule_id || "—"}</div>
                            {(log.method || log.path) && (
                              <div className="col-span-2 truncate"><span className="text-muted-foreground">request</span>: {log.method} {log.path} {log.status_code ? `→ ${log.status_code}` : ""} {log.duration_ms != null ? `(${log.duration_ms}ms)` : ""}</div>
                            )}
                          </div>
                          <div className="mt-2 text-xs whitespace-pre-wrap break-words">{log.message}</div>
                          {log.details && (
                            <pre className="mt-2 bg-background border rounded p-2 text-[11px] font-mono overflow-x-auto">
                              {JSON.stringify(log.details, null, 2)}
                            </pre>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Showing {items.length ? offset + 1 : 0}–{offset + items.length} of {total}</span>
        <div className="flex items-center gap-2">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            className="px-3 py-1.5 border rounded-md disabled:opacity-40 hover:bg-muted transition-colors">Prev</button>
          <button disabled={offset + LIMIT >= total} onClick={() => setOffset(offset + LIMIT)}
            className="px-3 py-1.5 border rounded-md disabled:opacity-40 hover:bg-muted transition-colors">Next</button>
        </div>
      </div>
    </div>
  );
}
