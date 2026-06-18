"use client";

import { useState } from "react";

export interface CustomTimeRange {
  gte_ms: number;
  lte_ms: number;
}

interface TimeRangePickerProps {
  isOpen: boolean;
  onApply: (range: CustomTimeRange) => void;
  onCancel: () => void;
  initialGteMs?: number;
  initialLteMs?: number;
}

/**
 * FR-14: Custom date/time range picker with 24h warning dialog.
 * Uses native <input type="datetime-local"> — no external date library.
 */
export default function TimeRangePicker({
  isOpen,
  onApply,
  onCancel,
  initialGteMs,
  initialLteMs,
}: TimeRangePickerProps) {
  const now = new Date();
  const toLocalDatetime = (ms: number) => {
    const d = new Date(ms);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };

  const defaultFrom = toLocalDatetime(now.getTime() - 3600_000);
  const defaultTo = toLocalDatetime(now.getTime());

  const [fromStr, setFromStr] = useState(
    initialGteMs ? toLocalDatetime(initialGteMs) : defaultFrom
  );
  const [toStr, setToStr] = useState(
    initialLteMs ? toLocalDatetime(initialLteMs) : defaultTo
  );
  const [error, setError] = useState("");
  const [showWarning, setShowWarning] = useState(false);
  const [pendingRange, setPendingRange] = useState<CustomTimeRange | null>(null);

  if (!isOpen) return null;

  function handleApply() {
    setError("");

    const fromMs = new Date(fromStr).getTime();
    const toMs = new Date(toStr).getTime();

    if (isNaN(fromMs) || isNaN(toMs)) {
      setError("Invalid date/time values.");
      return;
    }
    if (toMs <= fromMs) {
      setError("'To' must be greater than 'From'.");
      return;
    }
    if (toMs > Date.now()) {
      setError("'To' cannot be in the future.");
      return;
    }

    const rangeMs = toMs - fromMs;
    const rangeSec = rangeMs / 1000;

    // FR-14: Warning for ranges > 24 hours
    if (rangeSec > 86400) {
      setPendingRange({ gte_ms: fromMs, lte_ms: toMs });
      setShowWarning(true);
      return;
    }

    onApply({ gte_ms: fromMs, lte_ms: toMs });
  }

  function handleConfirmWarning() {
    if (pendingRange) {
      onApply(pendingRange);
    }
    setShowWarning(false);
    setPendingRange(null);
  }

  function handleCancelWarning() {
    setShowWarning(false);
    setPendingRange(null);
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/30"
        onClick={onCancel}
      />

      {/* Picker dialog */}
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-80 bg-card border rounded-lg shadow-xl p-5 space-y-4">
        <h3 className="text-sm font-semibold">Custom Time Range</h3>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">From</label>
            <input
              type="datetime-local"
              value={fromStr}
              onChange={(e) => setFromStr(e.target.value)}
              className="w-full px-3 py-1.5 text-sm border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">To</label>
            <input
              type="datetime-local"
              value={toStr}
              onChange={(e) => setToStr(e.target.value)}
              className="w-full px-3 py-1.5 text-sm border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        {error && (
          <p className="text-xs text-destructive">{error}</p>
        )}

        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs border rounded-md hover:bg-muted"
          >
            Cancel
          </button>
          <button
            onClick={handleApply}
            className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
          >
            Apply
          </button>
        </div>
      </div>

      {/* FR-14 Warning Dialog */}
      {showWarning && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/50" onClick={handleCancelWarning} />
          <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-[70] w-96 bg-card border border-warning/30 rounded-lg shadow-xl p-6 space-y-4">
            <div className="flex items-start gap-3">
              <span className="text-2xl">⚠️</span>
              <div>
                <h4 className="text-sm font-semibold text-warning-foreground">
                  High Resource Usage Warning
                </h4>
                <p className="text-xs text-muted-foreground mt-1">
                  Warning: Querying data beyond 24 hours may cause high resource usage on OpenSearch. Do you want to continue?
                </p>
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={handleCancelWarning}
                className="px-3 py-1.5 text-xs border rounded-md hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmWarning}
                className="px-3 py-1.5 text-xs bg-warning text-white rounded-md hover:bg-warning/90"
              >
                Continue Anyway
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
