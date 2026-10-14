"use client";

import { useMemo, useState } from "react";
import type { InterfaceBox, DeviceState } from "./types";
import { STATE_BOX, fmtSpeed } from "./types";

const FILTERS: { key: "all" | DeviceState; label: string }[] = [
  { key: "all", label: "All" },
  { key: "up", label: "🟩 Up" },
  { key: "down", label: "🟥 Down" },
  { key: "admin_down", label: "⬜ Admin" },
];

export function InterfaceBoxGrid({
  interfaces,
  selected,
  onSelect,
}: {
  interfaces: InterfaceBox[];
  selected: string | null; // if_name
  onSelect: (ifname: string | null) => void;
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
        // ponytail: plain grid for up to ~183 boxes — fast enough; react-window only if it bites.
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
          {shown.map((i) => {
            const isSel = selected === i.if_name;
            const operAdmin = [i.oper, i.admin].filter(Boolean).join("/") || "—";
            return (
              <button
                key={i.if_name}
                onClick={() => onSelect(isSel ? null : i.if_name)}
                title={i.if_name}
                className={`text-left p-2 rounded-md border text-xs transition ${STATE_BOX[i.state]} ${
                  isSel ? "ring-2 ring-offset-1 ring-current" : ""
                }`}
              >
                <div className="font-medium truncate">{i.if_name}</div>
                <div className="opacity-80">
                  {fmtSpeed(i.speed_mbps)} · {operAdmin}
                </div>
                {i.description ? (
                  <div className="opacity-70 truncate">{i.description}</div>
                ) : null}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
