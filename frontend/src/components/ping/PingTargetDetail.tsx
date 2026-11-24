"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, formatBucketLabelWIB, formatMs, formatPercent } from "@/lib/constants";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";
import { AreaChart } from "@/components/charts/AreaChart";
import { ChartFrame } from "@/components/network-devices/ChartFrame";
import type { Envelope } from "@/components/network-devices/types";
import type { PingSeriesData, PingTarget } from "./types";
import { STATUS_STYLE, fmtMs } from "./types";

const DEFAULT_PRESET = "1h";
const presetSeconds = (label: string) => TIME_PRESETS.find((p) => p.label === label)?.seconds ?? 3600;

// Time-range state lives in the URL (?range=1h | &gte_ms=…&lte_ms=…) so a shared link
// opens the same view. replaceState: no history spam (same as Network Devices syncUrl).
function writeRangeToUrl(preset: string, gte: number, lte: number) {
  const p = new URLSearchParams(window.location.search);
  if (preset === "custom") {
    p.delete("range");
    p.set("gte_ms", String(gte));
    p.set("lte_ms", String(lte));
  } else {
    p.set("range", preset);
    p.delete("gte_ms");
    p.delete("lte_ms");
  }
  window.history.replaceState(null, "", `${window.location.pathname}?${p.toString()}`);
}

function rangeLabel(g: number, l: number): string {
  const f = (ms: number) => {
    const d = new Date(ms);
    return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}`;
  };
  return `${f(g)} — ${f(l)}`;
}

// Detail view (page design §3): header + SD-WAN SLA time controls, stat tiles,
// Latency / Packet Loss / Jitter charts, context note.
export function PingTargetDetail({
  target,
  refreshMs,
  isAdmin,
  onBack,
  onEdit,
}: {
  target: PingTarget;
  refreshMs: number;
  isAdmin: boolean;
  onBack: () => void;
  onEdit: () => void;
}) {
  const [preset, setPreset] = useState(DEFAULT_PRESET);
  const [gteMs, setGteMs] = useState(() => Date.now() - presetSeconds(DEFAULT_PRESET) * 1000);
  const [lteMs, setLteMs] = useState(() => Date.now());
  const [customLabel, setCustomLabel] = useState<string | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [isZoomed, setIsZoomed] = useState(false);
  const preZoom = useRef<{ preset: string; gteMs: number; lteMs: number; customLabel: string | null } | null>(null);

  // Hydrate the range from the URL after mount (a useState initializer would mismatch SSR).
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const g = Number(p.get("gte_ms"));
    const l = Number(p.get("lte_ms"));
    const r = p.get("range");
    if (g && l && l > g) {
      setPreset("custom");
      setGteMs(g);
      setLteMs(l);
      setCustomLabel(rangeLabel(g, l));
    } else if (r && TIME_PRESETS.some((x) => x.label === r)) {
      setPreset(r);
    }
  }, []);

  // Live viewport: a preset slides to "now" on every refresh tick; a custom / zoomed
  // range is fixed in time, so refresh pauses (same behaviour as SD-WAN SLA).
  const live = preset !== "custom";
  useEffect(() => {
    if (!live) return;
    const tick = () => {
      const now = Date.now();
      setGteMs(now - presetSeconds(preset) * 1000);
      setLteMs(now);
    };
    tick();
    if (refreshMs <= 0) return;
    const id = setInterval(tick, refreshMs);
    return () => clearInterval(id);
  }, [live, preset, refreshMs]);

  function applyPreset(label: string) {
    setPreset(label);
    setCustomLabel(null);
    setShowPicker(false);
    setIsZoomed(false);
    const now = Date.now();
    writeRangeToUrl(label, now - presetSeconds(label) * 1000, now);
  }

  function applyCustom(r: CustomTimeRange) {
    setPreset("custom");
    setGteMs(r.gte_ms);
    setLteMs(r.lte_ms);
    setCustomLabel(rangeLabel(r.gte_ms, r.lte_ms));
    setShowPicker(false);
    writeRangeToUrl("custom", r.gte_ms, r.lte_ms);
  }

  function applyZoom(g: number, l: number) {
    if (!isZoomed) {
      preZoom.current = { preset, gteMs, lteMs, customLabel };
      setIsZoomed(true);
    }
    applyCustom({ gte_ms: g, lte_ms: l });
  }

  function resetZoom() {
    const s = preZoom.current;
    setIsZoomed(false);
    if (!s) return;
    setPreset(s.preset);
    setGteMs(s.gteMs);
    setLteMs(s.lteMs);
    setCustomLabel(s.customLabel);
    writeRangeToUrl(s.preset, s.gteMs, s.lteMs);
  }

  const { data, isLoading } = useSWR<Envelope<PingSeriesData>>(
    `/api/v1/ping/targets/${target.id}/series?gte_ms=${gteMs}&lte_ms=${lteMs}`,
    swrFetcher,
    { keepPreviousData: true },
  );
  const series = data?.data;
  const stats = series?.stats;
  const bucketMs = (series?.bucket_seconds ?? 60) * 1000;
  const chartData = (series?.points ?? []).map((p, i, arr) => ({
    timestamp: formatBucketLabelWIB(p.ts_ms, i > 0 ? arr[i - 1].ts_ms : null),
    tsMs: p.ts_ms,
    "Avg latency": p.avg_ms,
    "Max latency": p.max_ms,
    "Packet loss": p.loss_pct,
    Jitter: p.jitter_ms,
  }));
  const st = STATUS_STYLE[target.status];

  return (
    <div>
      {/* Header — DeviceDetail identity row + SD-WAN SLA time controls */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button onClick={onBack} className="text-sm text-muted-foreground hover:text-foreground">
          ← Ping targets
        </button>
        <div className="flex items-center gap-2 text-sm">
          <span className={`inline-block w-2 h-2 rounded-full ${st.dot}`} />
          <span className="font-semibold">{target.display_name}</span>
          <span className="text-muted-foreground">
            {target.url} · {target.cluster.toUpperCase()}
          </span>
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${st.badge}`}>
            {st.label}
          </span>
          {isAdmin ? (
            <button onClick={onEdit} className="px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted/50">
              ✎ Edit
            </button>
          ) : null}
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          {isZoomed && (
            <button
              onClick={resetZoom}
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted/50 transition-colors"
              title="Restore the view before drag-zoom"
            >
              ⟲ Reset zoom
            </button>
          )}
          <div className="flex gap-1 bg-muted rounded-md p-1">
            {TIME_PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => applyPreset(p.label)}
                className={cn(
                  "px-2.5 py-1 text-xs rounded-sm transition-colors",
                  preset === p.label ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {p.label}
              </button>
            ))}
            <button
              onClick={() => setShowPicker(true)}
              className={cn(
                "px-2.5 py-1 text-xs rounded-sm transition-colors",
                preset === "custom" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
              title={customLabel || "Select custom date/time range"}
            >
              {preset === "custom" && customLabel
                ? customLabel.length > 20 ? customLabel.slice(0, 18) + "…" : customLabel
                : "Custom"}
            </button>
          </div>
        </div>
      </div>

      {/* Stat tiles — OverviewCards.Kpi markup */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Kpi label="Avg latency" value={fmtMs(stats?.avg_ms ?? null)} />
        <Kpi
          label="Min / Max"
          value={stats?.min_ms != null ? `${fmtMs(stats.min_ms)} / ${fmtMs(stats.max_ms)}` : "—"}
        />
        <Kpi label="Jitter" value={fmtMs(stats?.jitter_ms ?? null)} />
        <Kpi
          label="Availability"
          value={stats?.availability_pct != null ? `${stats.availability_pct.toFixed(2)}%` : "—"}
        />
      </div>

      <div className="space-y-4">
        <PingChart title="Latency (ms)" loading={isLoading && !series} data={chartData} bucketMs={bucketMs}
          categories={["Avg latency", "Max latency"]} colors={["#3b82f6", "#f97316"]} unit="ms" onRangeSelect={applyZoom} />
        <PingChart title="Packet Loss (%)" loading={isLoading && !series} data={chartData} bucketMs={bucketMs}
          categories={["Packet loss"]} colors={["#ef4444"]} unit="%" onRangeSelect={applyZoom} />
        <PingChart title="Jitter (ms)" loading={isLoading && !series} data={chartData} bucketMs={bucketMs}
          categories={["Jitter"]} colors={["#8b5cf6"]} unit="ms" onRangeSelect={applyZoom} />
      </div>

      <ContextNote target={target} />

      <TimeRangePicker
        isOpen={showPicker}
        onApply={applyCustom}
        onCancel={() => setShowPicker(false)}
        initialGteMs={gteMs}
        initialLteMs={lteMs}
      />
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums mt-1">{value}</div>
    </div>
  );
}

// Chart card — SD-WAN SlaTimeseriesChart markup, body wrapped in ChartFrame (⤢ full screen).
function PingChart({
  title, loading, data, categories, colors, unit, bucketMs, onRangeSelect,
}: {
  title: string;
  loading: boolean;
  data: Record<string, string | number | null>[];
  categories: string[];
  colors: string[];
  unit: "ms" | "%";
  bucketMs: number;
  onRangeSelect: (g: number, l: number) => void;
}) {
  const hasData = data.some((d) => categories.some((c) => d[c] != null));
  // Shared formatters (same as SD-WAN SLA) — short labels so the Y axis never wraps.
  const fmt = (v: number) => (v == null ? "—" : unit === "%" ? formatPercent(v) : formatMs(v));
  return (
    <div className="bg-card border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3 pr-8">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="hidden sm:inline text-[11px] text-muted-foreground">drag to zoom</span>
      </div>
      {loading ? (
        <div className="h-48 bg-muted animate-pulse rounded" />
      ) : hasData ? (
        <ChartFrame title={title} inlineClassName="h-48 [&_text]:fill-gray-500 dark:[&_text]:fill-gray-400">
          <AreaChart
            className="h-full"
            data={data}
            index="timestamp"
            categories={categories}
            colors={colors}
            valueFormatter={fmt}
            showLegend={categories.length > 1}
            showGridLines={false}
            autoMinValue={unit === "ms"}
            allowDecimals
            curveType="monotone"
            showGradient={false}
            tickGap={30}
            yAxisWidth={60}
            onRangeSelect={onRangeSelect}
            bucketMs={bucketMs}
          />
        </ChartFrame>
      ) : (
        <p className="text-xs text-muted-foreground text-center py-8">No data</p>
      )}
    </div>
  );
}

function ContextNote({ target }: { target: PingTarget }) {
  let text: string | null = null;
  if (target.status === "expected_down") text = "Expected down — inactive DR link. Excluded from incident counts.";
  else if (target.status === "disabled") text = "Monitoring disabled — values shown are historical only.";
  else if (target.status === "down") {
    text = target.last_seen_ms
      ? `No ICMP reply on the latest probe (${new Date(target.last_seen_ms).toLocaleString("en-GB", { timeZone: "Asia/Jakarta" })} WIB).`
      : "No ICMP reply on the latest probe.";
  } else if (target.status === "no_data") text = "No probe data in the last 15 minutes — check the telegraf ping collector.";
  return text ? (
    <p className="text-xs text-muted-foreground border-t border-border pt-3 mt-4">{text}</p>
  ) : null;
}
