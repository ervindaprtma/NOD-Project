"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { swrFetcher, apiFetch, hasMinRole } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { NotificationChannelRead, MaintenanceWindow } from "@/types";

type Tab = "notifications" | "maintenance";

export default function ConfigurationsPage() {
  const [tab, setTab] = useState<Tab>("notifications");
  const canAdmin = hasMinRole("admin");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Configurations</h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-muted rounded-md p-1 w-fit">
        <button
          onClick={() => setTab("notifications")}
          className={cn(
            "px-4 py-1.5 text-sm rounded-sm transition-colors",
            tab === "notifications"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          Notification Channels
        </button>
        <button
          onClick={() => setTab("maintenance")}
          className={cn(
            "px-4 py-1.5 text-sm rounded-sm transition-colors",
            tab === "maintenance"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          Maintenance Windows
        </button>
      </div>

      {tab === "notifications" && <NotificationChannelsTab canAdmin={canAdmin} />}
      {tab === "maintenance" && <MaintenanceWindowsTab canAdmin={canAdmin} />}
    </div>
  );
}

// ── Notification Channels Tab (v3 §3.13) ──────────────────────

function NotificationChannelsTab({ canAdmin }: { canAdmin: boolean }) {
  const { data, error, isLoading } = useSWR<{ data: NotificationChannelRead[] }>(
    "/api/v1/config/notifications",
    swrFetcher
  );
  const channels = data?.data || [];

  async function toggleChannel(channel: string, enabled: boolean) {
    try {
      await apiFetch(`/api/v1/config/notifications/${channel}`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      });
      mutate("/api/v1/config/notifications");
    } catch (e: unknown) {
      alert(`Failed: ${(e as Error).message}`);
    }
  }

  return (
    <div className="bg-card border rounded-lg overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50 text-muted-foreground">
              <th className="text-left py-3 px-3 text-xs font-medium">Channel</th>
              <th className="text-left py-3 px-3 text-xs font-medium">Min Severity</th>
              <th className="text-center py-3 px-3 text-xs font-medium">Status</th>
              {canAdmin && <th className="text-right py-3 px-3 text-xs font-medium">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <tr key={i} className="border-b animate-pulse">
                  <td colSpan={canAdmin ? 4 : 3}>
                    <div className="h-10 bg-muted rounded" />
                  </td>
                </tr>
              ))
            ) : channels.length === 0 ? (
              <tr>
                <td colSpan={canAdmin ? 4 : 3} className="py-12 text-center text-muted-foreground">
                  No notification channels configured.
                </td>
              </tr>
            ) : (
              channels.map((ch) => (
                <tr key={ch.channel} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                  <td className="py-3 px-3 font-medium capitalize">{ch.channel}</td>
                  <td className="py-3 px-3 text-xs">{ch.min_severity || "—"}</td>
                  <td className="py-3 px-3 text-center">
                    <span
                      className={cn(
                        "inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium",
                        ch.enabled
                          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : "bg-muted text-muted-foreground"
                      )}
                    >
                      {ch.enabled ? "Active" : "Disabled"}
                    </span>
                  </td>
                  {canAdmin && (
                    <td className="py-3 px-3 text-right">
                      <button
                        onClick={() => toggleChannel(ch.channel, !ch.enabled)}
                        className={cn(
                          "px-2 py-1 text-[11px] rounded border bg-background hover:bg-muted transition-colors",
                          ch.enabled
                            ? "border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20"
                            : "border-emerald-200 text-emerald-700 hover:bg-emerald-50 dark:hover:bg-emerald-950/20"
                        )}
                      >
                        {ch.enabled ? "Disable" : "Enable"}
                      </button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Maintenance Windows Tab (v3 §3.14) ─────────────────────────

function MaintenanceWindowsTab({ canAdmin }: { canAdmin: boolean }) {
  const { data, error, isLoading } = useSWR<{ data: MaintenanceWindow[] }>(
    "/api/v1/config/maintenance",
    swrFetcher
  );
  const windows = data?.data || [];

  const [showCreate, setShowCreate] = useState(false);
  const [newSite, setNewSite] = useState("");
  const [newStart, setNewStart] = useState("");
  const [newEnd, setNewEnd] = useState("");
  const [newReason, setNewReason] = useState("");

  async function createWindow() {
    if (!newSite || !newStart || !newEnd) return;
    try {
      await apiFetch("/api/v1/config/maintenance", {
        method: "POST",
        body: JSON.stringify({
          site_name: newSite,
          starts_at: new Date(newStart).toISOString(),
          ends_at: new Date(newEnd).toISOString(),
          reason: newReason,
        }),
      });
      setShowCreate(false);
      setNewSite(""); setNewStart(""); setNewEnd(""); setNewReason("");
      mutate("/api/v1/config/maintenance");
    } catch (e: unknown) {
      alert(`Failed: ${(e as Error).message}`);
    }
  }

  async function deleteWindow(id: string) {
    if (!confirm("Delete this maintenance window?")) return;
    try {
      await apiFetch(`/api/v1/config/maintenance/${id}`, { method: "DELETE" });
      mutate("/api/v1/config/maintenance");
    } catch (e: unknown) {
      alert(`Failed: ${(e as Error).message}`);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Maintenance windows suppress alert evaluation for specific sites during planned outages.
        </p>
        {canAdmin && (
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            {showCreate ? "Cancel" : "+ New Window"}
          </button>
        )}
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className="bg-card border rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium">Site Name</label>
              <input
                type="text"
                value={newSite}
                onChange={(e) => setNewSite(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                placeholder="DC, DRC, Office"
              />
            </div>
            <div>
              <label className="text-xs font-medium">Reason</label>
              <input
                type="text"
                value={newReason}
                onChange={(e) => setNewReason(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                placeholder="e.g. Planned maintenance"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium">Starts At</label>
              <input
                type="datetime-local"
                value={newStart}
                onChange={(e) => setNewStart(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
              />
            </div>
            <div>
              <label className="text-xs font-medium">Ends At</label>
              <input
                type="datetime-local"
                value={newEnd}
                onChange={(e) => setNewEnd(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
              />
            </div>
          </div>
          <button
            onClick={createWindow}
            disabled={!newSite || !newStart || !newEnd}
            className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            Create
          </button>
        </div>
      )}

      {/* Windows Table */}
      <div className="bg-card border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-muted-foreground">
                <th className="text-left py-3 px-3 text-xs font-medium">Site</th>
                <th className="text-left py-3 px-3 text-xs font-medium">Reason</th>
                <th className="text-left py-3 px-3 text-xs font-medium">Starts</th>
                <th className="text-left py-3 px-3 text-xs font-medium">Ends</th>
                <th className="text-center py-3 px-3 text-xs font-medium">Status</th>
                {canAdmin && <th className="text-right py-3 px-3 text-xs font-medium">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b animate-pulse">
                    <td colSpan={canAdmin ? 6 : 5}><div className="h-10 bg-muted rounded" /></td>
                  </tr>
                ))
              ) : windows.length === 0 ? (
                <tr>
                  <td colSpan={canAdmin ? 6 : 5} className="py-12 text-center text-muted-foreground">
                    No maintenance windows scheduled.
                  </td>
                </tr>
              ) : (
                windows.map((w) => {
                  const now = new Date();
                  const start = new Date(w.starts_at);
                  const end = new Date(w.ends_at);
                  const status = now < start ? "Upcoming" : now > end ? "Past" : "Active";
                  const statusColor =
                    status === "Active"
                      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
                      : status === "Upcoming"
                        ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400"
                        : "bg-muted text-muted-foreground";
                  return (
                    <tr key={w.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                      <td className="py-3 px-3 font-medium">{w.site_name}</td>
                      <td className="py-3 px-3 text-xs text-muted-foreground">{w.reason || "—"}</td>
                      <td className="py-3 px-3 text-[11px]">{start.toLocaleString()}</td>
                      <td className="py-3 px-3 text-[11px]">{end.toLocaleString()}</td>
                      <td className="py-3 px-3 text-center">
                        <span className={cn("px-2 py-0.5 rounded-full text-[11px] font-medium", statusColor)}>
                          {status}
                        </span>
                      </td>
                      {canAdmin && (
                        <td className="py-3 px-3 text-right">
                          <button
                            onClick={() => deleteWindow(w.id)}
                            className="px-2 py-1 text-[11px] rounded border border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20 transition-colors"
                          >
                            Delete
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}