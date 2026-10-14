"use client";

import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import type { Envelope } from "./types";
import { FGT_SITE_NAME } from "./types";
import type { ResourceData } from "@/types";

// FGT-only box-health (CPU / Mem / Sessions / HA). Reuses the existing /resources
// endpoint — switches have no such telemetry, so this renders only for FortiGate.
// ponytail: current-value stat tiles (not timelines); sparkline charts are the upgrade
// path if the per-metric history is wanted here too.
export function FortiGateBoxHealth({ site }: { site: string | null }) {
  const siteName = site ? FGT_SITE_NAME[site] : undefined;
  const { data, error, isLoading } = useSWR<Envelope<ResourceData>>(
    siteName ? `/api/v1/resources?window=15m&site_name=${siteName}` : null,
    siteName ? (url: string) => swrFetcherLong(url) : null,
    { revalidateOnFocus: false },
  );

  if (!siteName) return null;

  const current = data?.data.current || [];

  return (
    <div className="rounded-md border border-border p-3 mb-4">
      <h3 className="text-sm font-semibold mb-3">Box Health</h3>
      {isLoading ? (
        <p className="text-xs text-muted-foreground">Loading box health…</p>
      ) : error ? (
        <p className="text-xs text-red-500">Failed to load box health.</p>
      ) : current.length === 0 ? (
        <p className="text-xs text-muted-foreground">No resource data.</p>
      ) : (
        <div className="space-y-3">
          {current.map((d) => (
            <div key={d.device}>
              {current.length > 1 ? (
                <div className="text-xs font-medium mb-1">
                  {d.hostname || d.device}
                  {d.sync_status ? (
                    <span className="ml-2 text-muted-foreground">
                      HA: {d.sync_status === "1" || d.sync_status.toLowerCase().includes("sync") ? "● in-sync" : "○ out-of-sync"}
                    </span>
                  ) : null}
                </div>
              ) : null}
              <div className="grid grid-cols-3 gap-2">
                <Tile label="CPU" value={pct(d.cpu_usage)} />
                <Tile label="Memory" value={pct(d.mem_usage)} />
                <Tile label="Sessions" value={d.session_count != null ? d.session_count.toLocaleString() : "—"} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function pct(v: number | undefined): string {
  return v != null ? `${v.toFixed(0)}%` : "—";
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/40 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}
