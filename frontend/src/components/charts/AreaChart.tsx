"use client";

import {
  AreaChart as RechartsAreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

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
}: AreaChartProps) {
  return (
    <div className={className}>
      <ResponsiveContainer width="100%" height="100%">
        <RechartsAreaChart
          data={data}
          margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
        >
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
        </RechartsAreaChart>
      </ResponsiveContainer>
    </div>
  );
}
