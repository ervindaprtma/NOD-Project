"use client";

import { useState, useMemo, Fragment } from "react";
import useSWR from "swr";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

const SEV: Record<string, string> = {
  CRITICAL: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 border-red-300",
  WARNING: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 border-amber-300",
  INFO: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400 border-blue-300",
};

interface LogRow {
  id: string;
  rule_id: string | null;
  rule_name: string;
  severity: string;
  metric_value_at_firing: number;
  fired_at: string;
  resolved_at: string | null;
  event_code: string | null;
  event_type: string | null;
}

// Point-event labels for the State column (VPN session / device reboot). Threshold
// rules leave event_type null and fall back to the Firing/Resolved logic below.
const EVENT_LABEL: Record<string, { text: string; cls: string }> = {
  connected: { text: "🟢 Connected", cls: "text-emerald-600" },
  disconnected: { text: "⚪ Disconnected", cls: "text-slate-500" },
  rebooted: { text: "🔁 Rebooted", cls: "text-amber-600" },
  // Counter wrap (32-bit SNMP sys_uptime rollover) — informational, not an outage.
  // Matches device_uptime.scan_reboots' "possible counter wrap" note.
  wrap: { text: "⟲ Counter wrap", cls: "text-blue-600" },
};

interface LogDetail extends LogRow {
  rule_snapshot: Record<string, any>;
  sent_payloads: Record<string, any>;
}

const wib = (iso: string) => new Date(iso).toLocaleString("en-GB", { timeZone: "Asia/Jakarta" });

function duration(a: string, b: string): string {
  const m = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60000));
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ""}`;
}

const LIMIT = 50;

/** Alert firing/resolved history — searchable, role-open (read-only). `ruleId` pre-filters
 *  to one rule ("track this rule" from the rules table). */
export function AlertHistory({ ruleId }: { ruleId?: string }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);

  const key = useMemo(() => {
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim());
    if (ruleId) p.set("rule_id", ruleId);
    if (status !== "all") p.set("status", status);
    if (severity !== "all") p.set("severity", severity);
    p.set("limit", String(LIMIT));
    p.set("offset", String(offset));
    return `/api/v1/alerts/logs?${p.toString()}`;
  }, [q, ruleId, status, severity, offset]);

  const { data, isLoading } = useSWR<{ data: { items: LogRow[]; total: number } }>(
    key, swrFetcher, { refreshInterval: 15000, keepPreviousData: true });
  const items = data?.data?.items || [];
  const total = data?.data?.total || 0;
  const reset = () => setOffset(0);

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <input value={q} onChange={(e) => { setQ(e.target.value); reset(); }}
          placeholder="search rule name / event code…"
          className="px-3 py-1.5 text-sm border rounded-md bg-background w-64 focus:outline-none focus:ring-1 focus:ring-primary" />
        <select value={status} onChange={(e) => { setStatus(e.target.value); reset(); }}
          className="px-2.5 py-1.5 text-sm border rounded-md bg-background">
          <option value="all">All states</option>
          <option value="firing">Firing</option>
          <option value="resolved">Resolved</option>
          <option value="connected">VPN Connected</option>
          <option value="disconnected">VPN Disconnected</option>
          <option value="rebooted">Rebooted</option>
          <option value="wrap">Counter wrap</option>
        </select>
        <select value={severity} onChange={(e) => { setSeverity(e.target.value); reset(); }}
          className="px-2.5 py-1.5 text-sm border rounded-md bg-background">
          <option value="all">All severity</option>
          <option value="CRITICAL">Critical</option>
          <option value="WARNING">Warning</option>
          <option value="INFO">Info</option>
        </select>
        <span className="text-xs text-muted-foreground ml-auto">{total} events</span>
      </div>

      {/* Table */}
      <div className="bg-card border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-muted-foreground text-xs">
                <th className="text-left py-2.5 px-3">Event Code</th>
                <th className="text-left py-2.5 px-3">Rule</th>
                <th className="text-left py-2.5 px-3 w-24">Severity</th>
                <th className="text-left py-2.5 px-3 w-44">Fired (WIB)</th>
                <th className="text-left py-2.5 px-3 w-40">State</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && items.length === 0 ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b animate-pulse">
                    {[1, 2, 3, 4, 5].map((j) => (
                      <td key={j} className="py-3 px-3"><div className="h-4 bg-muted rounded" /></td>
                    ))}
                  </tr>
                ))
              ) : items.length === 0 ? (
                <tr><td colSpan={5} className="py-10 text-center text-muted-foreground">No alert history</td></tr>
              ) : (
                items.map((log) => (
                  <Fragment key={log.id}>
                    <tr onClick={() => setOpenId(openId === log.id ? null : log.id)}
                      className="border-b last:border-0 hover:bg-muted/30 cursor-pointer transition-colors">
                      <td className="py-2.5 px-3 font-mono text-xs">{log.event_code || "—"}</td>
                      <td className="py-2.5 px-3 font-medium">{log.rule_name}</td>
                      <td className="py-2.5 px-3">
                        <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-medium border", SEV[log.severity] || "")}>
                          {log.severity}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-xs font-mono whitespace-nowrap">{wib(log.fired_at)}</td>
                      <td className="py-2.5 px-3 text-xs">
                        {log.event_type
                          ? <span className={EVENT_LABEL[log.event_type]?.cls || "text-muted-foreground"}>
                              {EVENT_LABEL[log.event_type]?.text || log.event_type}
                            </span>
                          : log.resolved_at
                          ? <span className="text-emerald-600">🟢 Resolved ({duration(log.fired_at, log.resolved_at)})</span>
                          : <span className="text-red-600">🔴 Firing</span>}
                      </td>
                    </tr>
                    {openId === log.id && (
                      <tr className="border-b bg-muted/20">
                        <td colSpan={5} className="p-0"><HistoryDetail id={log.id} /></td>
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
        <div className="flex gap-2">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            className="px-3 py-1.5 border rounded-md disabled:opacity-40 hover:bg-muted">Prev</button>
          <button disabled={offset + LIMIT >= total} onClick={() => setOffset(offset + LIMIT)}
            className="px-3 py-1.5 border rounded-md disabled:opacity-40 hover:bg-muted">Next</button>
        </div>
      </div>
    </div>
  );
}

function HistoryDetail({ id }: { id: string }) {
  const { data } = useSWR<{ data: LogDetail }>(`/api/v1/alerts/logs/${id}`, swrFetcher);
  const d = data?.data;
  if (!d) return <div className="p-4 text-xs text-muted-foreground">Loading…</div>;

  const snap = d.rule_snapshot || {};
  const clauses: any[] = snap.clauses || [];
  const resolvedClauses: any[] = snap.resolved_clauses || [];
  const rc = (mf: string, tk: string) => resolvedClauses.find((c) => c.metric_field === mf && c.target_key === tk);
  const payloads = d.sent_payloads || {};

  return (
    <div className="p-4 space-y-3 text-xs">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1">
        <div><span className="text-muted-foreground">Event</span>: <span className="font-mono">{d.event_code || "—"}</span></div>
        <div className="truncate"><span className="text-muted-foreground">Rule ID</span>: <span className="font-mono">{d.rule_id || "(deleted)"}</span></div>
        <div><span className="text-muted-foreground">Site</span>: {snap.site_name || snap.target_name || "—"}</div>
        <div><span className="text-muted-foreground">Fired</span>: {wib(d.fired_at)}</div>
        <div><span className="text-muted-foreground">Resolved</span>: {d.resolved_at ? wib(d.resolved_at) : "—"}</div>
        {d.resolved_at && <div><span className="text-muted-foreground">Duration</span>: {duration(d.fired_at, d.resolved_at)}</div>}
      </div>

      {/* Per-metric detail (was → now) — threshold rules only; point events (VPN
          session / reboot) carry their full detail in the Telegram body below. */}
      {!d.event_type && (
      <div>
        <div className="font-semibold mb-1">Metrics</div>
        <table className="w-full text-[11px] border rounded">
          <thead><tr className="text-muted-foreground border-b">
            <th className="text-left py-1 px-2">Metric</th><th className="text-left py-1 px-2">Target</th>
            <th className="text-right py-1 px-2">At fire</th><th className="text-right py-1 px-2">At resolve</th>
            <th className="text-right py-1 px-2">Threshold</th><th className="text-left py-1 px-2">Fired?</th>
          </tr></thead>
          <tbody>
            {(clauses.length ? clauses : [{ metric_field: snap.metric_field, target_key: "", value: d.metric_value_at_firing, threshold_value: snap.threshold_value, condition: snap.condition, breached: true }]).map((c, i) => {
              const now = rc(c.metric_field, c.target_key);
              return (
                <tr key={i} className="border-b last:border-0">
                  <td className="py-1 px-2 font-mono">{c.metric_field}</td>
                  <td className="py-1 px-2">{c.target_key || "—"}</td>
                  <td className="py-1 px-2 text-right font-mono">{typeof c.value === "number" ? c.value.toFixed(2) : "—"}</td>
                  <td className="py-1 px-2 text-right font-mono">{now && typeof now.value === "number" ? now.value.toFixed(2) : "—"}</td>
                  <td className="py-1 px-2 text-right font-mono">{c.condition} {c.threshold_value}</td>
                  <td className="py-1 px-2">{c.breached ? "🔴" : "🟢"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      )}

      {/* Telegram content sent — keyed by whatever event(s) this row recorded:
          firing/resolved for threshold rules, or the point-event name. */}
      <div className="space-y-2">
        <div className="font-semibold">📨 Sent to Telegram</div>
        {Object.keys(payloads).map((k) => {
          const p = payloads[k];
          if (!p) return null;
          return (
            <div key={k} className="border rounded p-2">
              <div className="flex items-center gap-2 mb-1">
                <span className="uppercase text-[10px] font-semibold">{k}</span>
                <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-medium",
                  p.ok ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                       : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400")}>
                  {p.ok ? "sent" : "send failed"}
                </span>
                <span className="text-muted-foreground">{(p.channels || []).join(", ")} · {p.sent_at}</span>
              </div>
              <pre className="whitespace-pre-wrap break-words bg-background border rounded p-2 text-[11px] max-h-60 overflow-y-auto">{p.body || p.subject}</pre>
            </div>
          );
        })}
        {Object.keys(payloads).length === 0 && (
          <p className="text-muted-foreground">No delivery record (older event, or notification not yet sent).</p>
        )}
        <a href={`/dashboard/system-logs?category=notify`}
          className="text-primary hover:underline inline-block">View delivery trail in System Logs →</a>
      </div>
    </div>
  );
}
