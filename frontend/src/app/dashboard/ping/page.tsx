"use client";

import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";
import { hasMinRole, swrFetcher } from "@/lib/api";
import { DEFAULT_REFRESH_MS, REFRESH_INTERVALS } from "@/lib/constants";
import { DegradedBanner } from "@/components/panels/DegradedBanner";
import type { Envelope } from "@/components/network-devices/types";
import type { Cluster, PingTargetsData } from "@/components/ping/types";
import { PingKpis } from "@/components/ping/PingKpis";
import { PingTargetTable } from "@/components/ping/PingTargetTable";
import { PingTargetDetail } from "@/components/ping/PingTargetDetail";
import { AddTargetModal } from "@/components/ping/AddTargetModal";
import { EditTargetModal } from "@/components/ping/EditTargetModal";

// Ping Monitor (plans/2026-11-24_ping-monitor-page-design.md). List → full-page detail
// on one route, like Network Devices. URL: ?target=<id> opens detail, ?site=dc|drc filters.
export default function PingMonitorPage() {
  const [targetId, setTargetId] = useState<string | null>(null);
  const [site, setSite] = useState<Cluster | "all">("all");
  const [refreshMs, setRefreshMs] = useState(DEFAULT_REFRESH_MS);
  const [showAdd, setShowAdd] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);  // target in the Edit modal
  const [isAdmin, setIsAdmin] = useState(false);

  // Hydrate from URL + role after mount (reading them in a useState initializer mismatches SSR).
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setTargetId(p.get("target"));
    const s = p.get("site");
    if (s === "dc" || s === "drc") setSite(s);
    setIsAdmin(hasMinRole("admin"));
  }, []);

  const syncUrl = useCallback((id: string | null, s: Cluster | "all") => {
    const p = new URLSearchParams(window.location.search);
    if (id) p.set("target", id);
    else {
      p.delete("target");
      p.delete("range");   // detail-only params
      p.delete("gte_ms");
      p.delete("lte_ms");
    }
    if (s === "all") p.delete("site");
    else p.set("site", s);
    const qs = p.toString();
    window.history.replaceState(null, "", qs ? `${window.location.pathname}?${qs}` : window.location.pathname);
  }, []);

  const openTarget = (id: string | null) => {
    setTargetId(id);
    syncUrl(id, site);
  };
  const changeSite = (s: Cluster | "all") => {
    setSite(s);
    syncUrl(targetId, s);
  };

  const { data, error, isLoading, mutate } = useSWR<Envelope<PingTargetsData>>(
    "/api/v1/ping/targets",
    swrFetcher,
    { refreshInterval: refreshMs },
  );
  const all = data?.data?.targets ?? [];
  const visible = site === "all" ? all : all.filter((t) => t.cluster === site);
  const target = targetId ? all.find((t) => t.id === targetId) : undefined;
  const editing = editId ? all.find((t) => t.id === editId) : undefined;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Ping Monitor</h1>
        <div className="flex items-center gap-2">
          {isAdmin && !targetId ? (
            <button onClick={() => setShowAdd(true)} className="px-3 py-1 text-xs rounded-md bg-primary text-primary-foreground">
              + Add target
            </button>
          ) : null}
          <select
            value={refreshMs}
            onChange={(e) => setRefreshMs(Number(e.target.value))}
            title="Auto-refresh interval"
            className="h-7 px-2 text-xs border border-border/60 dark:border-border/40 rounded-md bg-card text-muted-foreground cursor-pointer shadow-sm dark:shadow-none dark:ring-1 dark:ring-white/20"
          >
            {REFRESH_INTERVALS.map((ri) => (
              <option key={ri.value} value={ri.value}>
                {ri.label === "Off" ? "⏸ Off" : `↻ ${ri.label}`}
              </option>
            ))}
          </select>
        </div>
      </div>

      <DegradedBanner metas={[data?.meta]} />

      {targetId ? (
        isLoading && !data ? (
          <p className="text-sm text-muted-foreground py-8 text-center">Loading target…</p>
        ) : target ? (
          <PingTargetDetail
            key={target.id}
            target={target}
            refreshMs={refreshMs}
            isAdmin={isAdmin}
            onBack={() => openTarget(null)}
            onEdit={() => setEditId(target.id)}
          />
        ) : (
          <div className="py-8 text-center space-y-2">
            <p className="text-sm text-red-500">Target not found.</p>
            <button onClick={() => openTarget(null)} className="text-sm text-muted-foreground hover:text-foreground">
              ← Ping targets
            </button>
          </div>
        )
      ) : isLoading && !data ? (
        <p className="text-sm text-muted-foreground py-8 text-center">Loading ping targets…</p>
      ) : error ? (
        <div className="text-center py-8 space-y-2">
          <p className="text-sm text-red-500">Couldn&apos;t load ping targets.</p>
          <button onClick={() => mutate()} className="text-xs px-3 py-1 rounded-md border border-border hover:bg-muted/50">
            ↻ Retry
          </button>
        </div>
      ) : all.length === 0 ? (
        <div className="text-center py-8 space-y-2">
          <p className="text-sm text-muted-foreground">
            {isAdmin ? "No ping targets yet." : "No ping targets yet. Ask an admin to add some."}
          </p>
          {isAdmin ? (
            <button onClick={() => setShowAdd(true)} className="px-3 py-1 text-xs rounded-md bg-primary text-primary-foreground">
              + Add target
            </button>
          ) : null}
        </div>
      ) : (
        <>
          <PingKpis targets={visible} />
          <PingTargetTable
            targets={visible}
            site={site}
            isAdmin={isAdmin}
            onSiteChange={changeSite}
            onSelect={(id) => openTarget(id)}
            onEdit={(t) => setEditId(t.id)}
            onChanged={() => mutate()}
          />
        </>
      )}

      {showAdd ? <AddTargetModal onClose={() => setShowAdd(false)} onAdded={() => mutate()} /> : null}
      {editing ? (
        <EditTargetModal
          target={editing}
          onClose={() => setEditId(null)}
          onSaved={() => mutate()}
          onDeleted={() => {
            setEditId(null);
            if (targetId === editing.id) openTarget(null);  // deleted from its own detail view
            mutate();
          }}
        />
      ) : null}
    </div>
  );
}
