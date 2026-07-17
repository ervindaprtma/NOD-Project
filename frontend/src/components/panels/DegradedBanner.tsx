/**
 * Shown when the API reports meta.degraded — an underlying OpenSearch query timed
 * out, tripped the cluster circuit breaker, or returned partial shard results.
 *
 * The backend deliberately never 500s a page over one bad query; it returns an empty
 * skeleton instead. That means the numbers alongside this banner are incomplete or
 * zeroed, and a "0 B" is indistinguishable from "no traffic" without it. Say so
 * explicitly rather than letting the dashboard present a zero as a measurement.
 */
"use client";

import type { ResponseMeta } from "@/types";

export function DegradedBanner({ metas }: { metas: (ResponseMeta | null | undefined)[] }) {
  const reasons = Array.from(
    new Set(
      metas
        .filter((m) => m?.degraded)
        .flatMap((m) => m?.partial_errors ?? [])
    )
  );

  if (!metas.some((m) => m?.degraded)) return null;

  return (
    <div
      role="alert"
      className="rounded-lg border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-sm"
    >
      <div className="flex items-start gap-2">
        <span aria-hidden="true" className="text-amber-500 leading-none mt-0.5">
          ⚠
        </span>
        <div className="space-y-1">
          <p className="font-medium text-amber-600 dark:text-amber-400">
            Data unavailable — showing incomplete results
          </p>
          <p className="text-muted-foreground text-xs">
            Some queries did not complete, so the values below are incomplete or missing.
            Any zero shown is unknown, not a measurement. Try a shorter time range or
            refresh.
          </p>
          {reasons.length > 0 && (
            <ul className="text-xs text-muted-foreground/80 font-mono pt-1 space-y-0.5">
              {reasons.map((r) => (
                <li key={r}>· {r}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
