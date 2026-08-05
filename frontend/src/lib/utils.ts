import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Case-insensitive substring filter with exclude support: a leading "-" negates
 * (e.g. "-admin" keeps rows whose value does NOT contain "admin"). Empty query → keep all.
 * Mirrors the server-side exclude filters for client-only lists (VPN, activity-logs).
 */
export function matchTextFilter(haystack: string, query: string): boolean {
  const neg = query.startsWith("-");
  const term = (neg ? query.slice(1) : query).trim().toLowerCase();
  if (!term) return true;
  const hit = haystack.toLowerCase().includes(term);
  return neg ? !hit : hit;
}

/**
 * Re-export shared formatting utilities and constants
 * so existing `@/lib/utils` imports continue to work.
 */
export {
  formatBytes,
  formatMs,
  formatNumber,
  formatPercent,
  getDefaultTimeRange,
  TIME_PRESETS,
  REFRESH_INTERVALS,
  DEFAULT_REFRESH_MS,
  CHART_COLORS,
} from "./constants";
