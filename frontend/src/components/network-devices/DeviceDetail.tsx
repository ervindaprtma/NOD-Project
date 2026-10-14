"use client";

import { useState } from "react";
import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import type { DeviceDetailData, Envelope } from "./types";
import { VENDOR_LABEL, STATE_DOT } from "./types";
import { InterfaceBoxGrid } from "./InterfaceBoxGrid";
import { InterfaceCharts } from "./InterfaceCharts";
import { StateHistory } from "./StateHistory";
import { FortiGateBoxHealth } from "./FortiGateBoxHealth";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";

const WINDOWS = ["12h", "24h", "7d", "30d"];

export function DeviceDetail({ source, onBack }: { source: string; onBack: () => void }) {
  const [window, setWindow] = useState("24h");
  // Single expanded interface: its chart + state history render inline under the row.
  const [selected, setSelected] = useState<string | null>(null);
  const toggleIface = (ifname: string) => setSelected((prev) => (prev === ifname ? null : ifname));
  // Custom range (null unless window === "custom"); passed to children as gte/lte,
  // which the chart/history/resources endpoints accept as an override of ?window=.
  const [custom, setCustom] = useState<CustomTimeRange | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const gteMs = window === "custom" ? custom?.gte_ms : undefined;
  const lteMs = window === "custom" ? custom?.lte_ms : undefined;

  const { data, error, isLoading } = useSWR<Envelope<DeviceDetailData>>(
    `/api/v1/network-devices/devices/${encodeURIComponent(source)}/interfaces`,
    swrFetcherLong,
    { refreshInterval: 60_000 },
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
        <div className="ml-auto inline-flex rounded-md border border-border overflow-hidden text-xs">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => { setWindow(w); setShowPicker(false); }}
              className={`px-2.5 py-1 transition ${window === w ? "bg-foreground text-background" : "hover:bg-muted/50"}`}
            >
              {w}
            </button>
          ))}
          <button
            onClick={() => setShowPicker(true)}
            title={custom ? `${new Date(custom.gte_ms).toLocaleString()} — ${new Date(custom.lte_ms).toLocaleString()}` : "Custom range"}
            className={`px-2.5 py-1 transition ${window === "custom" ? "bg-foreground text-background" : "hover:bg-muted/50"}`}
          >
            Custom
          </button>
        </div>
      </div>

      <TimeRangePicker
        isOpen={showPicker}
        onApply={(r) => { setCustom(r); setWindow("custom"); setShowPicker(false); }}
        onCancel={() => setShowPicker(false)}
        initialGteMs={custom?.gte_ms}
        initialLteMs={custom?.lte_ms}
      />

      {isLoading ? (
        <p className="text-sm text-muted-foreground py-8 text-center">Loading device…</p>
      ) : error || !dev ? (
        <p className="text-sm text-red-500 py-8 text-center">Device not found or no current data.</p>
      ) : (
        <>
          {dev.vendor === "fortigate" ? <FortiGateBoxHealth site={dev.site} window={window} gteMs={gteMs} lteMs={lteMs} /> : null}

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
                <InterfaceCharts source={source} iface={i} window={window} gteMs={gteMs} lteMs={lteMs} />
                <StateHistory source={source} window={window} gteMs={gteMs} lteMs={lteMs} ifName={i.if_name} />
              </>
            )}
          />
        </>
      )}
    </div>
  );
}
