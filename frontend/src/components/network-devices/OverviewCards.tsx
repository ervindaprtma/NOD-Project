"use client";

import type { DeviceRow, SummaryData } from "./types";
import { VENDOR_LABEL } from "./types";

const SITE_ORDER = ["DC", "DRC", "Office"];
const VENDOR_ACCENT: Record<string, string> = {
  fortigate: "border-l-amber-500",
  h3c: "border-l-sky-500",
  cisco: "border-l-teal-500",
};

export function OverviewCards({
  summary,
  onSelect,
}: {
  summary: SummaryData;
  onSelect: (source: string) => void;
}) {
  const sites = Object.keys(summary.by_site).sort(
    (a, b) => (SITE_ORDER.indexOf(a) + 1 || 99) - (SITE_ORDER.indexOf(b) + 1 || 99),
  );

  return (
    <div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <Kpi label="Devices Online" value={`${summary.devices_online} / ${summary.devices_total}`} />
        <Kpi label="Interfaces Up" value={summary.interfaces_up.toLocaleString()} />
        <Kpi label="Interfaces Down" value={summary.interfaces_down.toLocaleString()} />
        <Kpi label="Admin-Disabled" value={summary.interfaces_admin_down.toLocaleString()} />
      </div>

      {sites.map((site) => (
        <div key={site} className="mb-5">
          <h3 className="text-xs font-semibold text-muted-foreground mb-2 uppercase tracking-wide">{site}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {[...summary.by_site[site]]
              .sort((a, b) => Number(a.online) - Number(b.online) || a.vendor.localeCompare(b.vendor))
              .map((d) => (
                <DeviceCard key={d.source_ip} d={d} onSelect={onSelect} />
              ))}
          </div>
        </div>
      ))}
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

function DeviceCard({ d, onSelect }: { d: DeviceRow; onSelect: (source: string) => void }) {
  return (
    <button
      onClick={() => onSelect(d.source_ip)}
      className={`text-left rounded-lg border border-l-4 border-border p-3 hover:bg-muted/30 transition ${
        VENDOR_ACCENT[d.vendor] || "border-l-zinc-400"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="font-semibold text-sm flex items-center gap-1.5">
          <span className={`inline-block w-2 h-2 rounded-full ${d.online ? "bg-emerald-500" : "bg-red-500"}`} />
          {d.name}
        </span>
        <span className="text-[11px] text-muted-foreground">{VENDOR_LABEL[d.vendor] || d.vendor}</span>
      </div>
      <div className="text-xs text-muted-foreground mt-1">
        {d.online ? "Online" : "Offline"} · {d.source_ip}
      </div>
      <div className="text-xs mt-2">
        Ifaces: {d.counts.total} · 🟩{d.counts.up} 🟥{d.counts.down} ⬜{d.counts.admin_down}
      </div>
    </button>
  );
}
