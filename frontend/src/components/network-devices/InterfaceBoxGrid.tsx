"use client";

import { Fragment, useMemo, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { InterfaceBox, DeviceState } from "./types";
import { STATE_DOT } from "./types";

const FILTERS: { key: "all" | DeviceState; label: string }[] = [
  { key: "all", label: "All" },
  { key: "up", label: "🟩 Up" },
  { key: "down", label: "🟥 Down" },
  { key: "admin_down", label: "⬜ Admin" },
];

const cap = (v: string | null | undefined) => (v ? v.charAt(0).toUpperCase() + v.slice(1) : "—");

// Speed as 100Mbps / 1Gbps / 10Gbps (full units, per request — fmtSpeed is the short form).
function fmtSpeedFull(mbps: number | null): string {
  if (!mbps || mbps <= 0) return "—";
  if (mbps >= 1000) {
    const g = mbps / 1000;
    return `${g % 1 ? g.toFixed(1) : g}Gbps`;
  }
  return `${mbps}Mbps`;
}

export function InterfaceBoxGrid({
  interfaces,
  selected,
  onToggle,
  renderDetail,
}: {
  interfaces: InterfaceBox[];
  selected: string | null; // the one expanded interface
  onToggle: (ifname: string) => void;
  renderDetail: (iface: InterfaceBox) => ReactNode; // inline chart + history under the row
}) {
  const [filter, setFilter] = useState<"all" | DeviceState>("all");
  const [search, setSearch] = useState("");

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return interfaces.filter(
      (i) =>
        (filter === "all" || i.state === filter) &&
        (!q ||
          i.if_name.toLowerCase().includes(q) ||
          (i.description || "").toLowerCase().includes(q)),
    );
  }, [interfaces, filter, search]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`px-2.5 py-1 text-xs rounded-md border transition ${
              filter === f.key
                ? "bg-foreground text-background border-foreground"
                : "border-border text-muted-foreground hover:bg-muted/50"
            }`}
          >
            {f.label}
          </button>
        ))}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search interface…"
          className="ml-auto px-2.5 py-1 text-xs rounded-md border border-border bg-background w-48"
        />
      </div>
      {shown.length === 0 ? (
        <p className="text-sm text-muted-foreground py-6 text-center">No interfaces match.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-xs">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Name</th>
                <th className="text-left px-3 py-2 font-medium">Descriptions</th>
                <th className="text-left px-3 py-2 font-medium">Speed</th>
                <th className="text-left px-3 py-2 font-medium">Admin Status</th>
                <th className="text-left px-3 py-2 font-medium">Oper Status</th>
                <th className="text-center px-3 py-2 font-medium">Color</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((i) => {
                const isSel = selected === i.if_name;
                return (
                  <Fragment key={i.if_name}>
                    <tr
                      onClick={() => onToggle(i.if_name)}
                      className={cn(
                        "border-t border-border cursor-pointer transition hover:bg-muted/40",
                        isSel && "bg-muted/60",
                      )}
                    >
                      <td className="px-3 py-1.5 font-medium whitespace-nowrap">
                        <span className="inline-block w-3 text-muted-foreground">{isSel ? "▾" : "▸"}</span>
                        {i.if_name}
                      </td>
                      <td className="px-3 py-1.5 text-muted-foreground max-w-[22rem] truncate" title={i.description || ""}>
                        {i.description || "—"}
                      </td>
                      <td className="px-3 py-1.5 tabular-nums whitespace-nowrap">
                        {fmtSpeedFull(i.speed_mbps)}
                        <span className={cn("inline-block w-2 h-2 rounded-full ml-1.5 align-middle", STATE_DOT[i.state])} />
                      </td>
                      {/* Admin = configured enable/disable; informational, never drives color/count. */}
                      <td className="px-3 py-1.5 text-muted-foreground">{cap(i.admin)}</td>
                      {/* Oper = operational up/down; this is what the color + up/down counts use. */}
                      <td className="px-3 py-1.5">{cap(i.oper)}</td>
                      <td className="px-3 py-1.5 text-center">
                        <span className={cn("inline-block w-2.5 h-2.5 rounded-full", STATE_DOT[i.state])} />
                      </td>
                    </tr>
                    {isSel ? (
                      <tr className="border-t border-border bg-muted/20">
                        <td colSpan={6} className="p-3">{renderDetail(i)}</td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
