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

/** Format milliseconds for display. When alwaysMs is true, always show as ms (no µs/s conversion). */
export function formatMs(ms: number, alwaysMs?: boolean): string {
  if (alwaysMs) return `${ms.toFixed(1)} ms`;
  if (ms < 1) return `${(ms * 1000).toFixed(1)} µs`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
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
 * Bucket label formatter for traffic charts (WIB / Asia/Jakarta).
 *
 * Always emits "DD MMM HH:MM[:SS]" so users can read each bucket's date
 * unambiguously — important when the queried window does NOT match the
 * current local date (e.g. yesterday's traffic shown today).
 *
 * To reduce visual noise on dense intra-day charts, callers may pass
 * `alwaysDate=false` to drop the date prefix when the bucket is on the
 * SAME day as the previous bucket. The default is `alwaysDate=true`
 * because cross-day queries are common in network reports.
 *
 * @param ms              Epoch milliseconds of the bucket.
 * @param prevMs          Epoch milliseconds of the previous bucket (or null).
 * @param includeSeconds  Include HH:MM:SS (true) or just HH:MM (false).
 * @param alwaysDate      If true, always include "DD MMM " prefix.
 *                        If false, omit prefix when same day as prev bucket.
 *
 * Example output (alwaysDate=true) for a 24h cross-day window:
 *   "29 Jun 09:00:00", "29 Jun 10:00:00", ..., "29 Jun 23:00:00",
 *   "30 Jun 00:00:00", "30 Jun 01:00:00", ..., "30 Jun 08:00:00"
 *
 * Example output (alwaysDate=false) for the same window:
 *   "29 Jun 09:00:00", "10:00:00", ..., "23:00:00",
 *   "30 Jun 00:00:00", "01:00:00", ..., "08:00:00"
 */
export function formatBucketLabelWIB(
  ms: number,
  prevMs: number | null,
  includeSeconds: boolean = true,
  alwaysDate: boolean = true,
): string {
  if (!ms || isNaN(ms)) return "";

  const timeOpts: Intl.DateTimeFormatOptions = {
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {}),
    hour12: false,
    timeZone: "Asia/Jakarta",
  };

  // Compare dates in WIB by formatting YYYY-MM-DD in Jakarta.
  const dateKey = (d: Date) =>
    d.toLocaleDateString("en-CA", { timeZone: "Asia/Jakarta" }); // en-CA → YYYY-MM-DD

  const cur = new Date(ms);
  const prev = prevMs ? new Date(prevMs) : null;

  const time = cur.toLocaleTimeString("en-US", timeOpts);
  const shouldIncludeDate = alwaysDate || !prev || dateKey(cur) !== dateKey(prev);
  if (!shouldIncludeDate) return time;

  const date = cur.toLocaleDateString("en-US", {
    day: "2-digit",
    month: "short",
    timeZone: "Asia/Jakarta",
  });
  return `${date} ${time}`;
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
