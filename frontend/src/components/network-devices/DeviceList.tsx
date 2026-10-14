"use client";

import { useMemo, useState } from "react";
import type { DeviceRow } from "./types";
import { VENDOR_LABEL } from "./types";

const SITE_ORDER = ["DC", "DRC", "Office"];

export function DeviceList({
  devices,
  onSelect,
}: {
  devices: DeviceRow[];
  onSelect: (source: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [vendor, setVendor] = useState("all");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const grouped = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = devices.filter(
      (d) =>
        (vendor === "all" || d.vendor === vendor) &&
        (!q || d.name.toLowerCase().includes(q) || d.source_ip.includes(q)),
    );
    const by: Record<string, DeviceRow[]> = {};
    for (const d of filtered) (by[d.site || "Unknown"] ||= []).push(d);
    const sites = Object.keys(by).sort(
      (a, b) => (SITE_ORDER.indexOf(a) + 1 || 99) - (SITE_ORDER.indexOf(b) + 1 || 99),
    );
    return sites.map((s) => [s, by[s]] as const);
  }, [devices, search, vendor]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search device or IP…"
          className="px-2.5 py-1 text-xs rounded-md border border-border bg-background w-56"
        />
        <select
          value={vendor}
          onChange={(e) => setVendor(e.target.value)}
          className="px-2.5 py-1 text-xs rounded-md border border-border bg-background"
        >
          <option value="all">All vendors</option>
          <option value="fortigate">FortiGate</option>
          <option value="h3c">H3C</option>
          <option value="cisco">Cisco</option>
        </select>
      </div>

      <div className="rounded-md border border-border overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="text-left px-3 py-2 font-medium">Device</th>
              <th className="text-left px-3 py-2 font-medium">Source IP</th>
              <th className="text-right px-3 py-2 font-medium">Ifaces</th>
              <th className="text-right px-3 py-2 font-medium">🟩</th>
              <th className="text-right px-3 py-2 font-medium">🟥</th>
              <th className="text-right px-3 py-2 font-medium">⬜</th>
              <th className="text-left px-3 py-2 font-medium">Vendor</th>
            </tr>
          </thead>
          <tbody>
            {grouped.map(([site, rows]) => (
              <GroupRows
                key={site}
                site={site}
                rows={rows}
                collapsed={!!collapsed[site]}
                toggle={() => setCollapsed((c) => ({ ...c, [site]: !c[site] }))}
                onSelect={onSelect}
              />
            ))}
            {grouped.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-muted-foreground">
                  No devices match.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GroupRows({
  site,
  rows,
  collapsed,
  toggle,
  onSelect,
}: {
  site: string;
  rows: DeviceRow[];
  collapsed: boolean;
  toggle: () => void;
  onSelect: (source: string) => void;
}) {
  // offline first, then by vendor (plan §2).
  const sorted = [...rows].sort(
    (a, b) => Number(a.online) - Number(b.online) || a.vendor.localeCompare(b.vendor) || a.name.localeCompare(b.name),
  );
  return (
    <>
      <tr className="bg-muted/20 border-t border-border cursor-pointer" onClick={toggle}>
        <td colSpan={7} className="px-3 py-1.5 font-semibold">
          {collapsed ? "▸" : "▾"} {site} <span className="text-muted-foreground font-normal">({rows.length})</span>
        </td>
      </tr>
      {collapsed
        ? null
        : sorted.map((d) => (
            <tr
              key={d.source_ip}
              className="border-t border-border hover:bg-muted/30 cursor-pointer"
              onClick={() => onSelect(d.source_ip)}
            >
              <td className="px-3 py-1.5">
                <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${d.online ? "bg-emerald-500" : "bg-red-500"}`} />
                {d.name}
              </td>
              <td className="px-3 py-1.5 tabular-nums text-muted-foreground">{d.source_ip}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{d.counts.total}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{d.counts.up}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{d.counts.down}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{d.counts.admin_down}</td>
              <td className="px-3 py-1.5">{VENDOR_LABEL[d.vendor] || d.vendor}</td>
            </tr>
          ))}
    </>
  );
}
