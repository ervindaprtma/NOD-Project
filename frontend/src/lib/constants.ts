/**
 * Shared constants for dashboard pages.
 * Avoids duplication across 5+ page files.
 */

export interface TimePreset {
  label: string;
  seconds: number;
}

export const TIME_PRESETS: TimePreset[] = [
  { label: "15m", seconds: 15 * 60 },
  { label: "1h", seconds: 3600 },
  { label: "2h", seconds: 7200 },
  { label: "4h", seconds: 14400 },
  { label: "12h", seconds: 43200 },
  { label: "24h", seconds: 86400 },
];

export interface RefreshOption {
  label: string;
  value: number; // milliseconds; 0 = off
}

export const REFRESH_INTERVALS: RefreshOption[] = [
  { label: "Off", value: 0 },
  { label: "15s", value: 15_000 },
  { label: "30s", value: 30_000 },
  { label: "60s", value: 60_000 },
];

export const DEFAULT_REFRESH_MS = 60_000;
export const DEFAULT_TIME_SECONDS = 15 * 60; // 15 minutes

/** Chart color palette — consistent across all dashboard charts. */
export const CHART_COLORS = [
  "#3b82f6", // blue
  "#f59e0b", // amber
  "#10b981", // emerald
  "#ef4444", // red
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#84cc16", // lime
  "#f97316", // orange
  "#6366f1", // indigo
];

/** Compute default absolute time range (last 15 minutes). */
export function getDefaultTimeRange(): { gte_ms: number; lte_ms: number } {
  const now = Date.now();
  return { gte_ms: now - DEFAULT_TIME_SECONDS * 1000, lte_ms: now };
}

/** Format bytes to human-readable string. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

/** Format milliseconds for display. */
export function formatMs(ms: number): string {
  if (ms < 1) return `${(ms * 1000).toFixed(1)} µs`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/** Format milliseconds — always show as ms (no µs/s conversion). */
export function formatAlwaysMs(ms: number): string {
  return `${ms.toFixed(1)} ms`;
}

/** Format large numbers (K/M). */
export function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

/** Format percentage. */
export function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`;
}

/**
 * Shared Radix TabsTrigger className.
 * Active tab: white card with shadow on gray container.
 * Inactive tab: gray text with hover highlight.
 */
export const TAB_TRIGGER_CLASS =
  "px-4 py-2 text-sm font-medium rounded-md transition-all " +
  "data-[state=active]:bg-background data-[state=active]:shadow-sm data-[state=active]:text-foreground " +
  "data-[state=inactive]:text-muted-foreground data-[state=inactive]:hover:text-foreground data-[state=inactive]:hover:bg-muted/50";
