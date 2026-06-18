import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
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
