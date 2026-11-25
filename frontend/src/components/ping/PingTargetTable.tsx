"use client";

import { useMemo, useState, type MouseEvent } from "react";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Cluster, GroupKind, PingTarget } from "./types";
import { GROUP_LABEL, GROUP_ORDER, STATUS_RANK, STATUS_STYLE, fmtAge, fmtMs, fmtPct } from "./types";

// Target list (page design §2.4–§2.5): DeviceList toolbar + table, grouped by group_kind
// with collapsible group rows. Row click → detail view. Admins get the Alerts-page list
// controls: row checkboxes + bulk Enable / Disable / Delete toolbar, an ON/OFF pill per
// row, and Edit / Del row actions (same classes + confirm() flow as the alert rules table).
export function PingTargetTable({
  targets,
  site,
  isAdmin,
  onSiteChange,
  onSelect,
  onEdit,
  onChanged,
}: {
  targets: PingTarget[];
  site: Cluster | "all";
  isAdmin: boolean;
  onSiteChange: (s: Cluster | "all") => void;
  onSelect: (id: string) => void;
  onEdit: (t: PingTarget) => void;
  onChanged: () => void;
}) {
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const cols = isAdmin ? 11 : 8;

  const grouped = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = targets.filter(
      (t) => !q || t.display_name.toLowerCase().includes(q) || t.url.toLowerCase().includes(q),
    );
    return GROUP_ORDER.map((g) => [g, rows.filter((t) => t.group_kind === g).sort(worstFirst)] as const)
      .filter(([, list]) => list.length > 0);
  }, [targets, search]);
  const shown = grouped.reduce((n, [, list]) => n + list.length, 0);

  // Selection only ever covers the rows currently shown (search / site filter applied).
  const visibleIds = grouped.flatMap(([, list]) => list.map((t) => t.id));
  const selected = targets.filter((t) => selectedIds.has(t.id) && visibleIds.includes(t.id));
  const toggleSelect = (id: string) =>
    setSelectedIds((prev) => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });
  const toggleSelectAll = () =>
    setSelectedIds(selected.length === visibleIds.length ? new Set() : new Set(visibleIds));

  const patch = (id: string, body: object) =>
    apiFetch(`/api/v1/ping/targets/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  const remove = (id: string) => apiFetch(`/api/v1/ping/targets/${id}`, { method: "DELETE" });

  async function toggleTarget(t: PingTarget) {
    try { await patch(t.id, { enabled: !t.enabled }); onChanged(); }
    catch (e: unknown) { alert(`Failed: ${(e as Error).message}`); }
  }

  async function deleteTarget(t: PingTarget) {
    if (!confirm(`Delete ping target "${t.display_name}"?`)) return;
    try { await remove(t.id); onChanged(); }
    catch (e: unknown) { alert(`Failed: ${(e as Error).message}`); }
  }

  async function bulkSetEnabled(enabled: boolean) {
    const todo = selected.filter((t) => t.enabled !== enabled);
    if (todo.length === 0) return;
    setBulkBusy(true);
    try { await Promise.all(todo.map((t) => patch(t.id, { enabled }))); onChanged(); }
    catch (e: unknown) { alert(`Failed: ${(e as Error).message}`); }
    finally { setBulkBusy(false); }
  }

  async function bulkDelete() {
    if (selected.length === 0 || !confirm(`Delete ${selected.length} selected ping target(s)? This cannot be undone.`)) return;
    setBulkBusy(true);
    try { await Promise.all(selected.map((t) => remove(t.id))); setSelectedIds(new Set()); onChanged(); }
    catch (e: unknown) { alert(`Failed: ${(e as Error).message}`); }
    finally { setBulkBusy(false); }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search target or IP…"
          className="px-2.5 py-1 text-xs rounded-md border border-border bg-background w-56"
        />
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {(["all", "dc", "drc"] as const).map((s) => (
            <button
              key={s}
              onClick={() => onSiteChange(s)}
              className={`px-3 py-1 transition ${site === s ? "bg-foreground text-background" : "hover:bg-muted/50"}`}
            >
              {s === "all" ? "All" : s.toUpperCase()}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-muted-foreground">
          {shown} of {targets.length} targets
        </span>
      </div>

      {/* Bulk-action toolbar (admin) — same as the Alerts rules list. */}
      {isAdmin ? (
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span className="text-xs text-muted-foreground mr-1">
            {selected.length > 0 ? `${selected.length} selected` : "No rows selected"}
          </span>
          <button
            onClick={() => bulkSetEnabled(true)}
            disabled={bulkBusy || selected.length === 0}
            className="px-2.5 py-1 text-xs rounded border bg-background hover:bg-muted disabled:opacity-40"
          >
            Enable
          </button>
          <button
            onClick={() => bulkSetEnabled(false)}
            disabled={bulkBusy || selected.length === 0}
            className="px-2.5 py-1 text-xs rounded border bg-background hover:bg-muted disabled:opacity-40"
          >
            Disable
          </button>
          <button
            onClick={bulkDelete}
            disabled={bulkBusy || selected.length === 0}
            className="px-2.5 py-1 text-xs rounded border border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20 disabled:opacity-40"
          >
            Delete
          </button>
          {bulkBusy ? <span className="text-xs text-muted-foreground">working…</span> : null}
        </div>
      ) : null}

      <div className="rounded-md border border-border overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              {isAdmin ? (
                <th className="w-8 px-3 py-2 text-center">
                  <input type="checkbox" aria-label="Select all targets"
                    checked={visibleIds.length > 0 && selected.length === visibleIds.length}
                    ref={(el) => { if (el) el.indeterminate = selected.length > 0 && selected.length < visibleIds.length; }}
                    onChange={toggleSelectAll} className="cursor-pointer" />
                </th>
              ) : null}
              <th className="text-left px-3 py-2 font-medium">Target</th>
              <th className="text-left px-3 py-2 font-medium">URL</th>
              <th className="text-left px-3 py-2 font-medium">Site</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
              <th className="text-right px-3 py-2 font-medium">Latency</th>
              <th className="text-right px-3 py-2 font-medium">Loss</th>
              <th className="text-right px-3 py-2 font-medium">Jitter</th>
              <th className="text-right px-3 py-2 font-medium">Seen</th>
              {isAdmin ? (
                <>
                  <th className="text-center px-3 py-2 font-medium">Enabled</th>
                  <th className="text-right px-3 py-2 font-medium">Actions</th>
                </>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {grouped.map(([group, rows]) => (
              <GroupRows
                key={group}
                group={group}
                rows={rows}
                cols={cols}
                isAdmin={isAdmin}
                selectedIds={selectedIds}
                collapsed={!!collapsed[group]}
                toggle={() => setCollapsed((c) => ({ ...c, [group]: !c[group] }))}
                onSelect={onSelect}
                onToggleSelect={toggleSelect}
                onToggleEnabled={toggleTarget}
                onEdit={onEdit}
                onDelete={deleteTarget}
              />
            ))}
            {grouped.length === 0 ? (
              <tr>
                <td colSpan={cols} className="px-3 py-6 text-center text-muted-foreground">
                  No targets match.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function worstFirst(a: PingTarget, b: PingTarget): number {
  return (
    STATUS_RANK[a.status] - STATUS_RANK[b.status] ||
    (b.metrics.loss_pct ?? 0) - (a.metrics.loss_pct ?? 0) ||
    (b.metrics.avg_ms ?? 0) - (a.metrics.avg_ms ?? 0)
  );
}

function lossClass(t: PingTarget): string {
  if (t.expected_down || t.status === "disabled") return "text-muted-foreground";
  if ((t.metrics.loss_pct ?? 0) >= 100) return "text-red-600 dark:text-red-400";
  if (t.status === "degraded" && (t.metrics.loss_pct ?? 0) > 0) return "text-amber-600 dark:text-amber-400";
  return "";
}

function GroupRows({
  group,
  rows,
  cols,
  isAdmin,
  selectedIds,
  collapsed,
  toggle,
  onSelect,
  onToggleSelect,
  onToggleEnabled,
  onEdit,
  onDelete,
}: {
  group: GroupKind;
  rows: PingTarget[];
  cols: number;
  isAdmin: boolean;
  selectedIds: Set<string>;
  collapsed: boolean;
  toggle: () => void;
  onSelect: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onToggleEnabled: (t: PingTarget) => void;
  onEdit: (t: PingTarget) => void;
  onDelete: (t: PingTarget) => void;
}) {
  // Controls inside a clickable row must not also open the detail view.
  const stop = (e: MouseEvent) => e.stopPropagation();
  return (
    <>
      <tr className="bg-muted/20 border-t border-border cursor-pointer" onClick={toggle}>
        <td colSpan={cols} className="px-3 py-1.5 font-semibold">
          {collapsed ? "▸" : "▾"} {GROUP_LABEL[group]}{" "}
          <span className="text-muted-foreground font-normal">({rows.length})</span>
        </td>
      </tr>
      {collapsed
        ? null
        : rows.map((t) => {
            const st = STATUS_STYLE[t.status];
            return (
              <tr
                key={t.id}
                className={cn("border-t border-border hover:bg-muted/30 cursor-pointer", selectedIds.has(t.id) && "bg-primary/5")}
                onClick={() => onSelect(t.id)}
              >
                {isAdmin ? (
                  <td className="px-3 py-1.5 text-center" onClick={stop}>
                    <input type="checkbox" aria-label={`Select ${t.display_name}`}
                      checked={selectedIds.has(t.id)} onChange={() => onToggleSelect(t.id)} className="cursor-pointer" />
                  </td>
                ) : null}
                <td className="px-3 py-1.5">
                  <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${st.dot}`} />
                  <span className="font-medium">{t.display_name}</span>
                </td>
                <td className="px-3 py-1.5 font-mono tabular-nums text-muted-foreground">{t.url}</td>
                <td className="px-3 py-1.5">{t.cluster.toUpperCase()}</td>
                <td className="px-3 py-1.5">
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${st.badge}`}>
                    {st.label}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{fmtMs(t.metrics.avg_ms)}</td>
                <td className={`px-3 py-1.5 text-right tabular-nums ${lossClass(t)}`}>{fmtPct(t.metrics.loss_pct)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{fmtMs(t.metrics.jitter_ms)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{fmtAge(t.last_seen_ms)}</td>
                {isAdmin ? (
                  <>
                    <td className="px-3 py-1.5 text-center" onClick={stop}>
                      <button
                        onClick={() => onToggleEnabled(t)}
                        className={cn(
                          "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium transition-colors cursor-pointer",
                          t.enabled
                            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                            : "bg-muted text-muted-foreground",
                        )}
                      >
                        {t.enabled ? "ON" : "OFF"}
                      </button>
                    </td>
                    <td className="px-3 py-1.5 text-right" onClick={stop}>
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => onEdit(t)}
                          className="px-2 py-1 text-[11px] rounded border bg-background hover:bg-muted"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => onDelete(t)}
                          className="px-2 py-1 text-[11px] rounded border border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20"
                        >
                          Del
                        </button>
                      </div>
                    </td>
                  </>
                ) : null}
              </tr>
            );
          })}
    </>
  );
}
