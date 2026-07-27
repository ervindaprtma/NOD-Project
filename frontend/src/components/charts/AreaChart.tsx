"use client";

import { useState, useEffect, useId, useRef } from "react";
import {
  AreaChart as RechartsAreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { rangeFromIndices } from "@/lib/chartZoom";

// ── Chart color palette (CSS variables from globals.css) ──────────
const CHART_COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

// Also accept hex values directly
function resolveColor(color: string, index: number): string {
  if (color.startsWith("#") || color.startsWith("hsl")) return color;
  // Map common names to CSS variables
  const nameMap: Record<string, string> = {
    blue: CHART_COLORS[0],
    orange: CHART_COLORS[1],
    red: CHART_COLORS[2],
    green: CHART_COLORS[3],
    purple: CHART_COLORS[4],
    emerald: "hsl(var(--chart-4))",
    amber: CHART_COLORS[1],
    cyan: "hsl(187 100% 40%)",
    violet: CHART_COLORS[4],
  };
  return nameMap[color] || CHART_COLORS[index % CHART_COLORS.length];
}

// ── Custom Tooltip ────────────────────────────────────────────────
function DefaultTooltip({ active, payload, label, valueFormatter }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border bg-background p-3 shadow-md text-xs">
      <p className="font-medium mb-1.5 text-muted-foreground">{label}</p>
      {payload.map((entry: any, i: number) => (
        <div key={i} className="flex items-center gap-2 py-0.5">
          <span
            className="w-2.5 h-2.5 rounded-sm shrink-0"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-muted-foreground">{entry.name}:</span>
          <span className="font-medium ml-auto">
            {valueFormatter ? valueFormatter(entry.value) : entry.value?.toLocaleString()}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── AreaChart Props ───────────────────────────────────────────────
interface AreaChartProps {
  data: Record<string, any>[];
  categories: string[];
  index: string;
  colors?: string[];
  valueFormatter?: (value: number) => string;
  showLegend?: boolean;
  showGridLines?: boolean;
  showXAxis?: boolean;
  showYAxis?: boolean;
  className?: string;
  autoMinValue?: boolean;
  allowDecimals?: boolean;
  curveType?: "monotone" | "linear" | "step";
  showGradient?: boolean;
  tickGap?: number;
  yAxisWidth?: number;
  // Drag-to-select: when set, dragging horizontally selects a sub-range and
  // calls this with the mapped epoch bounds. Each datum must carry `tsMs`.
  onRangeSelect?: (gteMs: number, lteMs: number) => void;
  bucketMs?: number;
  // Shaded x-ranges drawn behind the series — e.g. a window where no data was
  // collected. Neutral + hatched on purpose: an absence of data is not a status,
  // and the texture keeps it distinguishable from the drag-selection band.
  bands?: { x1: any; x2: any; label?: string }[];
  // Vertical event markers — e.g. a device reboot.
  markers?: { x: any; label?: string }[];
}

// ── AreaChart Component ───────────────────────────────────────────
export function AreaChart({
  data,
  categories,
  index,
  colors,
  valueFormatter,
  showLegend = false,
  showGridLines = true,
  showXAxis = true,
  showYAxis = true,
  className,
  autoMinValue = false,
  allowDecimals = true,
  curveType = "monotone",
  showGradient = true,
  tickGap = 30,
  yAxisWidth = 60,
  onRangeSelect,
  bucketMs = 60_000,
  bands,
  markers,
}: AreaChartProps) {
  const hatchId = useId().replace(/:/g, "");
  const [startIdx, setStartIdx] = useState<number | null>(null);
  const [endIdx, setEndIdx] = useState<number | null>(null);
  const zoomable = !!onRangeSelect;

  // Refs so the window-level mouseup reads live values without re-binding.
  const startRef = useRef<number | null>(null);
  const endRef = useRef<number | null>(null);
  const dragRef = useRef({ data, bucketMs, onRangeSelect });
  dragRef.current = { data, bucketMs, onRangeSelect };

  // Finalize on window mouseup so a release *outside* the plot still zooms, and
  // wandering off the plot mid-drag doesn't cancel it (chart onMouseUp/Leave can't).
  useEffect(() => {
    if (!zoomable) return;
    const onUp = () => {
      if (startRef.current == null) return;
      const { data: d, bucketMs: bm, onRangeSelect: cb } = dragRef.current;
      const r = rangeFromIndices(d.map((x) => Number(x.tsMs) || 0), startRef.current, endRef.current, bm);
      startRef.current = null;
      endRef.current = null;
      setStartIdx(null);
      setEndIdx(null);
      if (r && cb) cb(r.gteMs, r.lteMs);
    };
    window.addEventListener("mouseup", onUp);
    return () => window.removeEventListener("mouseup", onUp);
  }, [zoomable]);

  const dragProps = zoomable
    ? {
        onMouseDown: (e: any) => {
          if (e && e.activeTooltipIndex != null) {
            startRef.current = e.activeTooltipIndex;
            endRef.current = e.activeTooltipIndex;
            setStartIdx(e.activeTooltipIndex);
            setEndIdx(e.activeTooltipIndex);
          }
        },
        onMouseMove: (e: any) => {
          if (startRef.current != null && e && e.activeTooltipIndex != null) {
            endRef.current = e.activeTooltipIndex;
            setEndIdx(e.activeTooltipIndex);
          }
        },
      }
    : {};

  const bandLeft = startIdx != null && endIdx != null ? data[Math.min(startIdx, endIdx)]?.[index] : null;
  const bandRight = startIdx != null && endIdx != null ? data[Math.max(startIdx, endIdx)]?.[index] : null;

  return (
    <div className={className} style={zoomable ? { cursor: "crosshair", userSelect: "none" } : undefined}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsAreaChart
          data={data}
          margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
          {...dragProps}
        >
          {!!bands?.length && (
            <defs>
              <pattern
                id={`hatch-${hatchId}`}
                patternUnits="userSpaceOnUse"
                width="6"
                height="6"
                patternTransform="rotate(45)"
              >
                <rect width="6" height="6" fill="hsl(var(--muted-foreground))" fillOpacity={0.08} />
                <line x1="0" y1="0" x2="0" y2="6" stroke="hsl(var(--muted-foreground))" strokeWidth="2" strokeOpacity={0.28} />
              </pattern>
            </defs>
          )}
          {/* Behind the series: a gap is context, not data. */}
          {bands?.map((b, i) => (
            <ReferenceArea
              key={`band-${i}`}
              x1={b.x1}
              x2={b.x2}
              strokeOpacity={0}
              fill={`url(#hatch-${hatchId})`}
              label={b.label ? { value: b.label, position: "insideTop", fontSize: 9, fill: "hsl(var(--muted-foreground))" } : undefined}
            />
          ))}
          {showGridLines && (
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="hsl(var(--chart-grid))"
              vertical={false}
            />
          )}
          {showXAxis && (
            <XAxis
              dataKey={index}
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              tickLine={false}
              axisLine={false}
              interval="preserveStartEnd"
              minTickGap={tickGap}
            />
          )}
          {showYAxis && (
            <YAxis
              width={yAxisWidth}
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              tickLine={false}
              axisLine={false}
              tickFormatter={valueFormatter as any}
              allowDecimals={allowDecimals}
              domain={autoMinValue ? [0, "auto"] : undefined}
            />
          )}
          <Tooltip
            content={<DefaultTooltip valueFormatter={valueFormatter} />}
            // Explicit hover crosshair so the point under the pointer is obvious
            // before you start dragging — the default is easy to miss on a short plot.
            cursor={{
              stroke: "hsl(var(--muted-foreground))",
              strokeWidth: 1,
              strokeDasharray: "3 3",
              strokeOpacity: 0.6,
            }}
          />
          {showLegend && categories.length > 1 && (
            <Legend
              iconType="square"
              iconSize={8}
              wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
            />
          )}
          {categories.map((cat, i) => (
            <Area
              key={cat}
              type={curveType}
              dataKey={cat}
              name={cat}
              stroke={resolveColor(colors?.[i] || CHART_COLORS[i], i)}
              fill={resolveColor(colors?.[i] || CHART_COLORS[i], i)}
              fillOpacity={showGradient ? 0.15 : 0}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          ))}
          {markers?.map((m, i) => (
            <ReferenceLine
              key={`marker-${i}`}
              x={m.x}
              stroke="hsl(var(--chart-2))"
              strokeDasharray="3 3"
              strokeWidth={1.5}
              label={m.label ? { value: m.label, position: "top", fontSize: 9, fill: "hsl(var(--chart-2))" } : undefined}
            />
          ))}
          {zoomable && bandLeft != null && bandRight != null && bandLeft !== bandRight && (
            // The drag selection is chrome, not data: it wears the primary/UI hue
            // with a dashed border rather than a series colour, so it stays legible
            // whatever the series underneath is (blue on Bandwidth, emerald on
            // Availability) and never reads as another measurement.
            <ReferenceArea
              x1={bandLeft}
              x2={bandRight}
              fill="hsl(var(--primary))"
              fillOpacity={0.16}
              stroke="hsl(var(--primary))"
              strokeOpacity={0.55}
              strokeWidth={1}
              strokeDasharray="3 3"
            />
          )}
        </RechartsAreaChart>
      </ResponsiveContainer>
    </div>
  );
}
