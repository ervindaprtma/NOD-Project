"use client";

import useSWR from "swr";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Types (endpoint is admin-only; shapes are small, keep them local) ──
interface SourceHealth {
  name: string;
  kind: string | null;
  status: "healthy" | "stale" | "no_data" | "down";
  last_doc_ms: number | null;
  last_age_seconds: number | null;
  reachable: boolean;
  consecutive_fails: number;
  alerted: boolean;
  last_change: string | null;
}
interface SchedulerHealth { id: string; running: boolean; next_run: string | null }
interface LogQueue { depth: number; capacity: number; dropped: number; written: number }
interface HealthStatus {
  api: string;
  db: string;
  sources: SourceHealth[];
  watchdog_started_at: string;
  schedulers: SchedulerHealth[];
  log_queue: LogQueue;
}
interface LogRow { id: string; ts: string; level: string; event: string; message: string }

// ── Helpers ──
function relTime(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h${m % 60}m`;
  return `${Math.floor(h / 24)}d${h % 24}h`;
}
function prettyJob(id: string): string {
  return id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
const AMBER = "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400";
const STATUS_PILL: Record<string, string> = {
  healthy: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  stale: AMBER,
  no_data: AMBER,
  down: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  error: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
};
const STATUS_DOT: Record<string, string> = {
  healthy: "🟢", stale: "🟠", no_data: "🟠", down: "🔴", ok: "🟢", error: "🔴",
};
const STATUS_LABEL: Record<string, string> = {
  healthy: "fresh", stale: "stale", no_data: "no data", down: "down",
};

function Pill({ status, label }: { status: string; label?: string }) {
  return (
    <span className={cn("inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium capitalize",
      STATUS_PILL[status] || "bg-muted text-muted-foreground")}>
      {label || status}
    </span>
  );
}

export function SystemHealthTab() {
  const { data, error, isLoading } = useSWR<{ data: HealthStatus }>(
    "/api/v1/health/status", swrFetcher, { refreshInterval: 15_000 }
  );
  const { data: logs } = useSWR<{ data: { items: LogRow[] } }>(
    "/api/v1/logs/system?event=endpoint_down,endpoint_recovered&limit=10", swrFetcher,
    { refreshInterval: 30_000 }
  );

  const h = data?.data;
  const events = logs?.data?.items ?? [];

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm">
        <p className="font-medium text-red-600 dark:text-red-400">Can’t reach the backend</p>
        <p className="text-muted-foreground text-xs mt-1">
          The health API itself did not respond — the backend may be down or restarting.
        </p>
      </div>
    );
  }
  if (isLoading || !h) {
    return <div className="space-y-3">{[1, 2, 3].map((i) => <div key={i} className="h-24 bg-muted rounded animate-pulse" />)}</div>;
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">System Health</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Live status of data endpoints and background services. Refreshes every 15s.
        </p>
      </div>

      {/* ── OpenSearch Data Sources ── */}
      <section>
        <h3 className="text-xs font-semibold uppercase text-muted-foreground mb-2">OpenSearch Data Sources</h3>
        <div className="border rounded-lg overflow-hidden">
          {h.sources.length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">No sources reported yet.</div>
          ) : h.sources.map((c) => {
            const age = c.last_age_seconds;
            const lastData = age == null ? "no matching data"
              : age < 60 ? `${age}s ago`
              : age < 3600 ? `${Math.floor(age / 60)}m ago`
              : `${Math.floor(age / 3600)}h${Math.floor((age % 3600) / 60)}m ago`;
            return (
              <div key={c.name} className="flex items-center justify-between px-4 py-3 border-b last:border-0">
                <div className="flex items-center gap-2">
                  <span aria-hidden>{STATUS_DOT[c.status]}</span>
                  <span className="font-medium text-sm">{c.name}</span>
                  <Pill status={c.status} label={STATUS_LABEL[c.status]} />
                </div>
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  {c.alerted && <span className="text-red-600 dark:text-red-400">⚠ alert sent</span>}
                  <span>last data {lastData}</span>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── Core Services + Log Pipeline ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <section>
          <h3 className="text-xs font-semibold uppercase text-muted-foreground mb-2">Core Services</h3>
          <div className="border rounded-lg p-4 space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span>{STATUS_DOT[h.api]} API</span><Pill status={h.api} />
            </div>
            <div className="flex items-center justify-between">
              <span>{STATUS_DOT[h.db]} PostgreSQL</span><Pill status={h.db} />
            </div>
            <div className="border-t my-1" />
            {h.schedulers.length === 0 ? (
              <p className="text-xs text-muted-foreground">No schedulers reported.</p>
            ) : h.schedulers.map((s) => (
              <div key={s.id} className="flex items-center justify-between text-xs">
                <span>{s.running ? "🟢" : "🔴"} {prettyJob(s.id)}</span>
                <span className="text-muted-foreground">next {relTime(s.next_run)}</span>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h3 className="text-xs font-semibold uppercase text-muted-foreground mb-2">Log Pipeline</h3>
          <div className="border rounded-lg p-4 space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span>Queue depth</span>
              <span className="font-mono">{h.log_queue.depth} / {h.log_queue.capacity}</span>
            </div>
            <div className="flex items-center justify-between">
              <span>Dropped</span>
              <span className={cn("font-mono", h.log_queue.dropped > 0 && "text-red-600 dark:text-red-400")}>
                {h.log_queue.dropped}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span>Written</span><span className="font-mono">{h.log_queue.written}</span>
            </div>
          </div>
        </section>
      </div>

      {/* ── Recent health events ── */}
      <section>
        <h3 className="text-xs font-semibold uppercase text-muted-foreground mb-2">Recent Health Events</h3>
        <div className="border rounded-lg overflow-hidden">
          {events.length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">No endpoint up/down events recorded.</div>
          ) : events.map((e) => (
            <div key={e.id} className="flex items-start gap-3 px-4 py-2 border-b last:border-0 text-xs">
              <span aria-hidden>{e.event === "endpoint_down" ? "🔴" : "🟢"}</span>
              <span className="font-mono text-muted-foreground shrink-0">
                {new Date(e.ts).toLocaleString()}
              </span>
              <span className="flex-1">{e.message}</span>
            </div>
          ))}
          <a href="/dashboard/system-logs"
            className="block px-4 py-2 text-xs text-primary hover:underline border-t">
            View all in System Logs →
          </a>
        </div>
      </section>
    </div>
  );
}
