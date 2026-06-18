"use client";

import { useState, useCallback } from "react";
import type { CustomTimeRange } from "@/components/panels/TimeRangePicker";
import {
  TIME_PRESETS,
  REFRESH_INTERVALS,
  DEFAULT_REFRESH_MS,
  getDefaultTimeRange,
} from "@/lib/constants";

interface UseTimeRangeReturn {
  gteMs: number;
  lteMs: number;
  selectedPreset: string;
  refreshInterval: number;
  showCustomPicker: boolean;
  customRangeLabel: string | null;

  setShowCustomPicker: (v: boolean) => void;
  selectPreset: (label: string, seconds: number) => void;
  handleCustomApply: (range: CustomTimeRange) => void;
  setRefreshInterval: (v: number) => void;
}

/**
 * Shared hook for time range + auto-refresh management.
 * Fixes BUG: selecting a preset now re-enables auto-refresh at default interval.
 */
export function useTimeRange(): UseTimeRangeReturn {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [refreshInterval, setRefreshInterval] = useState(DEFAULT_REFRESH_MS);
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customRangeLabel, setCustomRangeLabel] = useState<string | null>(null);

  const selectPreset = useCallback((label: string, seconds: number) => {
    const now = Date.now();
    setGteMs(now - seconds * 1000);
    setLteMs(now);
    setSelectedPreset(label);
    setCustomRangeLabel(null);
    setShowCustomPicker(false);
    // FIX B1/B2: re-enable auto-refresh when selecting a preset
    if (refreshInterval === 0) {
      setRefreshInterval(DEFAULT_REFRESH_MS);
    }
  }, [refreshInterval]);

  const handleCustomApply = useCallback((range: CustomTimeRange) => {
    setGteMs(range.gte_ms);
    setLteMs(range.lte_ms);
    setSelectedPreset("custom");
    setRefreshInterval(0); // FR-14: auto-refresh OFF for fixed custom range
    setShowCustomPicker(false);

    const from = new Date(range.gte_ms);
    const to = new Date(range.lte_ms);
    const fmt = (d: Date) =>
      `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}`;
    setCustomRangeLabel(`${fmt(from)} — ${fmt(to)}`);
  }, []);

  return {
    gteMs,
    lteMs,
    selectedPreset,
    refreshInterval,
    showCustomPicker,
    customRangeLabel,
    setShowCustomPicker,
    selectPreset,
    handleCustomApply,
    setRefreshInterval,
  };
}
