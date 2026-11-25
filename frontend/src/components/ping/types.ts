// Ping Monitor API shapes (backend/app/api/ping.py).
import { formatMs, formatPercent } from "@/lib/constants";

export type PingStatus = "up" | "degraded" | "down" | "expected_down" | "disabled" | "no_data";
export type GroupKind = "internal" | "internet" | "dr";
export type Cluster = "dc" | "drc";

export interface PingMetrics {
  avg_ms: number | null;
  min_ms: number | null;
  max_ms: number | null;
  jitter_ms: number | null;
  loss_pct: number | null;
  availability_pct: number | null;
  samples: number;
}

export interface PingTarget {
  id: string;
  url: string;
  cluster: Cluster;
  display_name: string;
  custom_name: string | null;
  group_kind: GroupKind;
  expected_down: boolean;
  enabled: boolean;
  status: PingStatus;
  last_seen_ms: number | null;
  metrics: PingMetrics;
}

export interface PingTargetsData {
  window_ms: number;
  warn_loss_pct: number;
  warn_latency_ms: number;
  targets: PingTarget[];
}

export interface DiscoveredUrl {
  cluster: Cluster;
  url: string;
  target_name: string | null;
  last_seen_ms: number | null;
}

export interface PingPoint {
  ts_ms: number;
  avg_ms: number | null;
  max_ms: number | null;
  jitter_ms: number | null;
  loss_pct: number | null;
}

export interface PingSeriesData {
  gte_ms: number;
  lte_ms: number;
  bucket_seconds: number;
  stats: PingMetrics;
  points: PingPoint[];
}

// ── Shared presentational helpers (page design §2.5) ──

export const GROUP_ORDER: GroupKind[] = ["internal", "internet", "dr"];
export const GROUP_LABEL: Record<GroupKind, string> = {
  internal: "Internal",
  internet: "Internet",
  dr: "DR links",
};

export const STATUS_STYLE: Record<PingStatus, { dot: string; badge: string; label: string }> = {
  up: { dot: "bg-emerald-500", badge: "badge-success", label: "Up" },
  degraded: { dot: "bg-amber-500", badge: "badge-warning", label: "Degraded" },
  down: { dot: "bg-red-500", badge: "badge-danger", label: "Down" },
  expected_down: { dot: "bg-slate-400", badge: "badge-neutral", label: "Expected down" },
  disabled: { dot: "bg-slate-400", badge: "badge-neutral", label: "Disabled" },
  no_data: { dot: "bg-slate-400", badge: "badge-neutral", label: "No data" },
};

// Worst first inside a group.
export const STATUS_RANK: Record<PingStatus, number> = {
  down: 0, degraded: 1, no_data: 2, up: 3, expected_down: 4, disabled: 5,
};

// Null-safe wrappers over the shared formatters (failed probes have no latency).
export const fmtMs = (v: number | null): string => (v == null ? "—" : formatMs(v));
export const fmtPct = (v: number | null): string => (v == null ? "—" : formatPercent(v));

export function fmtAge(ms: number | null): string {
  if (ms == null) return "—";
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}
