"use client";

import { useCallback, useState } from "react";

/**
 * Map a pair of bucket indices to a real time range.
 * gte = start bucket's epoch; lte = end bucket's epoch + one bucket width
 * (so the last selected bucket is fully included). Returns null when the
 * selection is under one bucket or under 60s (backend's min bucket).
 */
export function rangeFromIndices(
  tsList: number[],
  i0: number | null,
  i1: number | null,
  bucketMs: number,
): { gteMs: number; lteMs: number } | null {
  if (i0 == null || i1 == null) return null;
  const a = Math.min(i0, i1);
  const b = Math.max(i0, i1);
  if (b - a < 1) return null;
  const gteMs = tsList[a];
  const lteMs = tsList[b] + bucketMs;
  if (!gteMs || !lteMs || lteMs - gteMs < 60_000) return null;
  return { gteMs, lteMs };
}

/**
 * Drag-to-select over a viewBox-scaled SVG chart. Spread `handlers` on the
 * <svg>; render `selRect` (in viewBox units) as a translucent <rect>. A plain
 * click (start === end) selects nothing, so tooltips/clicks keep working.
 */
export function useSvgDragSelect(opts: {
  tsList: number[];
  viewW: number;
  padLeft: number;
  plotW: number;
  bucketMs: number;
  onRangeSelect?: (gteMs: number, lteMs: number) => void;
}) {
  const { tsList, viewW, padLeft, plotW, bucketMs, onRangeSelect } = opts;
  const n = tsList.length;
  const [start, setStart] = useState<number | null>(null);
  const [end, setEnd] = useState<number | null>(null);

  const idxFromEvent = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const vbX = ((e.clientX - rect.left) / rect.width) * viewW;
      const step = plotW / Math.max(1, n);
      const i = Math.round((vbX - padLeft) / step);
      return Math.max(0, Math.min(n - 1, i));
    },
    [viewW, padLeft, plotW, n],
  );

  const step = plotW / Math.max(1, n);
  const selRect =
    start != null && end != null && start !== end
      ? { x: padLeft + Math.min(start, end) * step, width: Math.abs(end - start) * step + step }
      : null;

  const handlers = onRangeSelect
    ? {
        onPointerDown: (e: React.PointerEvent<SVGSVGElement>) => {
          const i = idxFromEvent(e);
          setStart(i);
          setEnd(i);
          e.currentTarget.setPointerCapture?.(e.pointerId);
        },
        onPointerMove: (e: React.PointerEvent<SVGSVGElement>) => {
          if (start == null) return;
          setEnd(idxFromEvent(e));
        },
        onPointerUp: () => {
          const r = rangeFromIndices(tsList, start, end, bucketMs);
          if (r) onRangeSelect(r.gteMs, r.lteMs);
          setStart(null);
          setEnd(null);
        },
        onPointerLeave: () => {
          setStart(null);
          setEnd(null);
        },
      }
    : {};

  return { selRect, dragging: start != null, handlers };
}
