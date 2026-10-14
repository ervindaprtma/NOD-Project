"use client";

import { useState } from "react";
import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Envelope } from "./types";
import type { DeviceAvailabilityData } from "@/types";

// Full device availability (restored from the old Resources → Availability tab):
// per-device reporting table with uptime detail + reboot/collector-gap history.
// Site-scoped (the /api/v1/device-uptime endpoint takes one site), window owns its
// own SLA range. No backend change. Per-device availability charts (old small
// multiples) are intentionally omitted — not part of this request.
const SITES: { key: string; label: string }[] = [
  { key: "Site_FGT-DC", label: "DC" },
  { key: "Site_FGT-DRC", label: "DRC" },
  { key: "Site_FGT_Office", label: "Office" },
];
const WINDOWS = ["24h", "7d", "30d", "90d", "365d"];

const STATUS_STYLE: Record<string, { label: string; icon: string; cls: string }> = {
  up: { label: "Up", icon: "●", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" },
  rebooted: { label: "Rebooted", icon: "⟳", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400" },
  not_reporting: { label: "Not reporting", icon: "◌", cls: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400" },
  collector_gap: { label: "Collector gap", icon: "▨", cls: "bg-muted text-muted-foreground" },
};

function fmtWIB(ms: number | null): string {
  if (!ms) return "—";
  return new Date(ms).toLocaleString("en-GB", {
    timeZone: "Asia/Jakarta", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
function fmtDur(seconds: number): string {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

export function AvailabilityCards() {
  const [site, setSite] = useState(SITES[0].key);
  const [win, setWin] = useState("24h");
  const label = SITES.find((s) => s.key === site)?.label ?? site;

  const { data, error, isLoading, isValidating, mutate } = useSWR<Envelope<DeviceAvailabilityData>>(
    `/api/v1/device-uptime?site_name=${site}&window=${win}`,
    swrFetcherLong,
    { revalidateOnFocus: false },
  );
  const sum = data?.data?.summary;
  const devices = data?.data?.devices ?? [];
  const degraded = data?.meta?.degraded;

  // Flatten reboots + collector gaps into one newest-first history (what happened, when).
  const events = [
    ...devices.flatMap((d) =>
      (d.reboots ?? []).map((r) => ({
        time: r.at_ms,
        device: d.hostname || d.device_key,
        vendor: d.vendor,
        kind: (r.note ? "Counter wrap" : "Reboot") as "Reboot" | "Counter wrap",
        detail: r.note ? "Uptime counter wrapped — not an outage." : "Device rebooted; collectors could not read it.",
      })),
    ),
    ...(sum?.collector_gaps ?? []).map((g) => ({
      time: g.end_ms, device: "— site-wide —", vendor: "",
      kind: "Collector gap" as const, detail: `Collector timeout, no data (${fmtDur(g.duration_seconds)}).`,
    })),
  ].sort((a, b) => b.time - a.time);

  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Device Availability</h3>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
            {SITES.map((s) => (
              <button key={s.key} onClick={() => setSite(s.key)}
                className={`px-3 py-1 transition ${site === s.key ? "bg-foreground text-background" : "hover:bg-muted/50"}`}>{s.label}</button>
            ))}
          </div>
          <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
            {WINDOWS.map((w) => (
              <button key={w} onClick={() => setWin(w)}
                className={`px-2.5 py-1 transition ${win === w ? "bg-foreground text-background" : "hover:bg-muted/50"}`}>{w}</button>
            ))}
          </div>
        </div>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground py-6 text-center">Loading device availability…</p>
      ) : error ? (
        <div className="text-center py-6 space-y-2">
          <p className="text-sm text-red-500">Couldn&apos;t load device availability.</p>
          <p className="text-xs text-muted-foreground">Wide windows (90d–365d) query a lot of data and can time out — try a shorter one.</p>
          <button onClick={() => mutate()} disabled={isValidating} className="text-xs px-3 py-1 rounded-md border border-border hover:bg-muted/50 disabled:opacity-50">{isValidating ? "Retrying…" : "↻ Retry"}</button>
        </div>
      ) : degraded ? (
        <div className="rounded-md border border-amber-300/60 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-900/20 p-3 flex items-center justify-between gap-3 text-sm text-amber-800 dark:text-amber-300">
          <span>⚠ The data source was slow or unavailable for this window — results may be incomplete.</span>
          <button onClick={() => mutate()} disabled={isValidating} className="text-xs px-3 py-1 rounded-md border border-amber-400/60 disabled:opacity-50">{isValidating ? "Retrying…" : "↻ Retry"}</button>
        </div>
      ) : !sum ? (
        <p className="text-sm text-muted-foreground py-6 text-center">No availability data for {label}.</p>
      ) : (
        <div className="space-y-4">
          {/* Summary — counts, not a fleet average (availability is per device) */}
          <div className="rounded-lg border border-border p-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
            <span className="font-medium">{sum.devices_reporting} / {sum.devices_total} reporting</span>
            <span className="text-muted-foreground">{sum.reboots_total} reboot{sum.reboots_total === 1 ? "" : "s"}</span>
            <span className="text-muted-foreground">
              {sum.collector_gaps.length} collector gap{sum.collector_gaps.length === 1 ? "" : "s"}
              {sum.collector_gap_seconds > 0 ? ` (${fmtDur(sum.collector_gap_seconds)})` : ""}
            </span>
            {sum.lowest_uptime_device ? (
              <span className="text-muted-foreground" title="Most recently booted — smallest uptime, not lowest availability">
                Lowest uptime <span className="text-foreground font-medium">{sum.lowest_uptime_device.hostname} {sum.lowest_uptime_device.uptime_human_short}</span>
              </span>
            ) : null}
            {sum.devices_partial_history > 0 ? <span className="text-muted-foreground">{sum.devices_partial_history} partial history</span> : null}
          </div>

          {/* Per-device reporting table */}
          {devices.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">No devices reporting uptime for {label}.</p>
          ) : (
            <div className="rounded-lg border border-border overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="text-left font-medium px-4 py-2.5">Device</th>
                    <th className="text-left font-medium px-4 py-2.5">Vendor</th>
                    <th className="text-left font-medium px-4 py-2.5">Status</th>
                    <th className="text-right font-medium px-4 py-2.5">Avail %</th>
                    <th className="text-right font-medium px-4 py-2.5">UP for</th>
                    <th className="text-left font-medium px-4 py-2.5">Booted (WIB)</th>
                    <th className="text-right font-medium px-4 py-2.5">Reboots</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => {
                    const st = STATUS_STYLE[d.status] ?? STATUS_STYLE.up;
                    return (
                      <tr key={d.device_key} className="border-b border-border/40 last:border-0 hover:bg-muted/30">
                        <td className="px-4 py-2.5">
                          <div className="font-medium">{d.hostname}</div>
                          <div className="text-xs text-muted-foreground">{d.device_key}</div>
                        </td>
                        <td className="px-4 py-2.5 text-muted-foreground">{d.vendor}</td>
                        <td className="px-4 py-2.5">
                          <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium", st.cls)}>
                            <span aria-hidden>{st.icon}</span>{st.label}
                          </span>
                          {d.partial_history ? <span className="ml-1 text-xs text-muted-foreground" title="Onboarded mid-window — availability measured from its first sample">◐</span> : null}
                          {d.wrap_risk ? <span className="ml-1 text-xs text-amber-600 dark:text-amber-400" title="SNMP sys_uptime 32-bit counter near wrap (~497 days) — logs as a wrap event, safe to ignore as a reboot.">⚠</span> : null}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums">
                          {d.availability_pct === null ? <span className="text-muted-foreground" title="Not enough history to judge">—</span> : `${d.availability_pct.toFixed(2)}%`}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums whitespace-nowrap">{d.uptime_human_long}</td>
                        <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap">{fmtWIB(d.boot_time_ms)}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums">
                          {d.reboot_count}
                          {d.total_downtime_seconds > 0 ? <div className="text-xs text-muted-foreground">{fmtDur(d.total_downtime_seconds)} down</div> : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Reboot & collector-gap history — what happened, when (newest first) */}
          <div className="rounded-lg border border-border overflow-x-auto">
            <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
              <h4 className="text-sm font-semibold">Reboot &amp; Event History</h4>
              <span className="text-xs text-muted-foreground">{events.length} event{events.length === 1 ? "" : "s"} in this window</span>
            </div>
            {events.length === 0 ? (
              <p className="px-4 py-6 text-sm text-muted-foreground text-center">No reboots or collector gaps in this window — every device stayed up.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground">
                    <th className="text-left font-medium px-4 py-2.5">When (WIB)</th>
                    <th className="text-left font-medium px-4 py-2.5">Device</th>
                    <th className="text-left font-medium px-4 py-2.5">Vendor</th>
                    <th className="text-left font-medium px-4 py-2.5">Event</th>
                    <th className="text-left font-medium px-4 py-2.5">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e, i) => {
                    const kindCls = e.kind === "Reboot" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                      : e.kind === "Collector gap" ? "bg-muted text-muted-foreground"
                        : "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
                    const icon = e.kind === "Reboot" ? "⟳" : e.kind === "Collector gap" ? "▨" : "↻";
                    return (
                      <tr key={`${e.time}-${e.device}-${i}`} className="border-b border-border/40 last:border-0 hover:bg-muted/30">
                        <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap tabular-nums">{fmtWIB(e.time)}</td>
                        <td className="px-4 py-2.5 font-medium">{e.device}</td>
                        <td className="px-4 py-2.5 text-muted-foreground">{e.vendor || "—"}</td>
                        <td className="px-4 py-2.5"><span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium", kindCls)}><span aria-hidden>{icon}</span>{e.kind}</span></td>
                        <td className="px-4 py-2.5 text-muted-foreground">{e.detail}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
