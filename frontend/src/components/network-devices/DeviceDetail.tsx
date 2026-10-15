"use client";

import { useState, useEffect, useRef } from "react";
import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import type { DeviceDetailData, Envelope } from "./types";
import { VENDOR_LABEL, STATE_DOT } from "./types";
import { InterfaceBoxGrid } from "./InterfaceBoxGrid";
import { InterfaceCharts } from "./InterfaceCharts";
import { StateHistory } from "./StateHistory";
import { FortiGateBoxHealth } from "./FortiGateBoxHealth";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";

// Time presets for the Devices tab — short ranges first, then the longer ones.
const PRESETS: { label: string; s: number }[] = [
  { label: "15m", s: 900 }, { label: "1h", s: 3600 }, { label: "2h", s: 7200 }, { label: "4h", s: 14400 },
  { label: "12h", s: 43200 }, { label: "24h", s: 86400 }, { label: "7d", s: 604800 }, { label: "30d", s: 2592000 },
];

export function DeviceDetail({ source, onBack, refreshMs = 0 }: { source: string; onBack: () => void; refreshMs?: number }) {
  // Single expanded interface: its chart + state history render inline under the row.
  const [selected, setSelected] = useState<string | null>(null);
  const toggleIface = (ifname: string) => setSelected((prev) => (prev === ifname ? null : ifname));
  // Time range: always drive children by gte/lte. activeSeconds>0 = a preset that slides
  // on refresh; 0 = a pinned range (custom or drag-zoom). preset is the label for the pills.
  const [preset, setPreset] = useState("15m");
  const [activeSeconds, setActiveSeconds] = useState(900);
  const [gteMs, setGteMs] = useState(() => Date.now() - 900 * 1000);
  const [lteMs, setLteMs] = useState(() => Date.now());
  const [showPicker, setShowPicker] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const preZoom = useRef<{ gteMs: number; lteMs: number; activeSeconds: number; preset: string } | null>(null);

  // Live viewport: slide a preset window forward on each refresh tick (pinned ranges don't).
  useEffect(() => {
    if (activeSeconds <= 0 || refreshMs <= 0) return;
    const id = setInterval(() => { const now = Date.now(); setGteMs(now - activeSeconds * 1000); setLteMs(now); }, refreshMs);
    return () => clearInterval(id);
  }, [activeSeconds, refreshMs]);

  function applyPreset(s: number, label: string) {
    const now = Date.now();
    setGteMs(now - s * 1000); setLteMs(now); setActiveSeconds(s); setPreset(label);
    setZoomed(false); setShowPicker(false);
  }
  function applyCustom(r: CustomTimeRange) {
    setGteMs(r.gte_ms); setLteMs(r.lte_ms); setActiveSeconds(0); setPreset("custom");
    setZoomed(false); setShowPicker(false);
  }
  // Drag-to-zoom from any interface chart → pin the dragged sub-range (remember prior view).
  function applyZoom(g: number, l: number) {
    if (!zoomed) preZoom.current = { gteMs, lteMs, activeSeconds, preset };
    setGteMs(g); setLteMs(l); setActiveSeconds(0); setPreset("custom"); setZoomed(true);
  }
  function resetZoom() {
    const p = preZoom.current;
    if (p && p.activeSeconds > 0) applyPreset(p.activeSeconds, p.preset);
    else if (p) applyCustom({ gte_ms: p.gteMs, lte_ms: p.lteMs });
    else setZoomed(false);
  }

  const { data, error, isLoading } = useSWR<Envelope<DeviceDetailData>>(
    `/api/v1/network-devices/devices/${encodeURIComponent(source)}/interfaces`,
    swrFetcherLong,
    { refreshInterval: refreshMs },
  );

  const dev = data?.data;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button onClick={onBack} className="text-sm text-muted-foreground hover:text-foreground">
          ← Back to Devices
        </button>
        {dev ? (
          <div className="flex items-center gap-2 text-sm">
            <span className={`inline-block w-2 h-2 rounded-full ${dev.online ? "bg-emerald-500" : "bg-red-500"}`} />
            <span className="font-semibold">{dev.name}</span>
            <span className="text-muted-foreground">
              · {dev.source_ip} · {dev.site || "—"} · {VENDOR_LABEL[dev.vendor] || dev.vendor}
            </span>
          </div>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          {zoomed ? (
            <button onClick={resetZoom} className="px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted/50">
              ⤡ Reset zoom
            </button>
          ) : null}
          <div className="inline-flex flex-wrap rounded-md border border-border overflow-hidden text-xs">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => applyPreset(p.s, p.label)}
                className={`px-2.5 py-1 transition ${!zoomed && preset === p.label ? "bg-foreground text-background" : "hover:bg-muted/50"}`}
              >
                {p.label}
              </button>
            ))}
            <button
              onClick={() => setShowPicker(true)}
              title="Custom range"
              className={`px-2.5 py-1 transition ${!zoomed && preset === "custom" ? "bg-foreground text-background" : "hover:bg-muted/50"}`}
            >
              Custom
            </button>
          </div>
        </div>
      </div>

      <TimeRangePicker
        isOpen={showPicker}
        onApply={applyCustom}
        onCancel={() => setShowPicker(false)}
        initialGteMs={gteMs}
        initialLteMs={lteMs}
      />

      {isLoading ? (
        <p className="text-sm text-muted-foreground py-8 text-center">Loading device…</p>
      ) : error || !dev ? (
        <p className="text-sm text-red-500 py-8 text-center">Device not found or no current data.</p>
      ) : (
        <>
          {dev.vendor === "fortigate" ? <FortiGateBoxHealth site={dev.site} window={preset} gteMs={gteMs} lteMs={lteMs} refreshMs={refreshMs} onRangeSelect={applyZoom} /> : null}

          <div className="flex items-center gap-3 text-xs text-muted-foreground mb-2">
            <span className="flex items-center gap-1"><i className={`w-2 h-2 rounded-full ${STATE_DOT.up}`} /> {dev.counts.up} up</span>
            <span className="flex items-center gap-1"><i className={`w-2 h-2 rounded-full ${STATE_DOT.down}`} /> {dev.counts.down} down</span>
            <span className="flex items-center gap-1"><i className={`w-2 h-2 rounded-full ${STATE_DOT.admin_down}`} /> {dev.counts.admin_down} admin</span>
          </div>

          <InterfaceBoxGrid
            interfaces={dev.interfaces}
            selected={selected}
            onToggle={toggleIface}
            renderDetail={(i) => (
              <>
                <InterfaceCharts source={source} iface={i} window={preset} gteMs={gteMs} lteMs={lteMs} refreshMs={refreshMs} onRangeSelect={applyZoom} />
                <StateHistory source={source} window={preset} gteMs={gteMs} lteMs={lteMs} ifName={i.if_name} refreshMs={refreshMs} />
              </>
            )}
          />
        </>
      )}
    </div>
  );
}
