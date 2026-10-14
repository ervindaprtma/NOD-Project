"use client";

import useSWR from "swr";
import { swrFetcherLong } from "@/lib/api";
import type { Envelope, HistoryData } from "./types";
import { fmtDuration } from "./types";

export function StateHistory({ source, window, gteMs, lteMs, ifName }: { source: string; window: string; gteMs?: number; lteMs?: number; ifName?: string }) {
  const range = gteMs != null && lteMs != null ? `gte_ms=${gteMs}&lte_ms=${lteMs}` : `window=${window}`;
  // Device-wide fetch (one SWR key per device+window, deduped); filter to one interface
  // when ifName is given, so the inline panel shows only that interface's changes.
  const { data, error, isLoading } = useSWR<Envelope<HistoryData>>(
    `/api/v1/network-devices/devices/${encodeURIComponent(source)}/history?${range}`,
    swrFetcherLong,
    { revalidateOnFocus: false },
  );

  const all = data?.data.transitions || [];
  const rows = ifName ? all.filter((t) => t.if_name === ifName) : all;

  return (
    <div className="mt-4">
      <h3 className="text-sm font-semibold mb-2">Interface State Changes</h3>
      {isLoading ? (
        <p className="text-xs text-muted-foreground py-4">Loading history…</p>
      ) : error ? (
        <p className="text-xs text-red-500 py-4">Failed to load state history.</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4">No state changes in window.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-xs">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Time (WIB)</th>
                <th className="text-left px-3 py-2 font-medium">Interface</th>
                <th className="text-left px-3 py-2 font-medium">Change</th>
                <th className="text-left px-3 py-2 font-medium">Held before</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t, idx) => (
                <tr key={`${t.if_name}-${t.at_ms}-${idx}`} className="border-t border-border">
                  <td className="px-3 py-1.5 tabular-nums">
                    {new Date(t.at_ms).toLocaleString("en-GB", {
                      timeZone: "Asia/Jakarta",
                      hour12: false,
                    })}
                  </td>
                  <td className="px-3 py-1.5 font-medium">{t.if_name}</td>
                  <td className="px-3 py-1.5 uppercase">
                    {t.from} → {t.to}
                  </td>
                  <td className="px-3 py-1.5">
                    {fmtDuration(t.duration_before_ms)}
                    {t.offline_gap ? (
                      <span className="ml-1 text-amber-600 dark:text-amber-400" title="Device was offline during this period — carried state, not blended">
                        ⚠ offline gap
                      </span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
