"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { apiFetch, getErrorMessage, swrFetcher } from "@/lib/api";
import type { Envelope } from "@/components/network-devices/types";
import type { Cluster, DiscoveredUrl, GroupKind } from "./types";
import { GROUP_LABEL, GROUP_ORDER, fmtAge } from "./types";
import { PingModal } from "./PingModal";

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;
const keyOf = (d: DiscoveredUrl) => `${d.cluster}|${d.url}`;

interface Draft { checked: boolean; name: string; group: GroupKind }

// Add-target picker (page design §4): urls OpenSearch reports for measurement=ping that
// are not curated yet. Each ticked row gets a display name + group, then one POST per row.
export function AddTargetModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const { data, isLoading, error } = useSWR<Envelope<DiscoveredUrl[]>>(
    "/api/v1/ping/discover", swrFetcher, { revalidateOnFocus: false },
  );
  const found = useMemo(() => data?.data ?? [], [data]);
  const [search, setSearch] = useState("");
  const [site, setSite] = useState<Cluster | "all">("all");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Defaults: collector's tag.target_name (else the url); DNS names → internet, IPs → internal.
  const draftOf = (d: DiscoveredUrl): Draft =>
    drafts[keyOf(d)] ?? { checked: false, name: d.target_name || d.url, group: IPV4.test(d.url) ? "internal" : "internet" };
  const setDraft = (d: DiscoveredUrl, patch: Partial<Draft>) =>
    setDrafts((prev) => ({ ...prev, [keyOf(d)]: { ...draftOf(d), ...patch } }));

  const q = search.trim().toLowerCase();
  const rows = found.filter(
    (d) => (site === "all" || d.cluster === site) &&
      (!q || d.url.toLowerCase().includes(q) || (d.target_name || "").toLowerCase().includes(q)),
  );
  const selected = found.filter((d) => drafts[keyOf(d)]?.checked);

  async function addSelected() {
    setSaving(true);
    setErr(null);
    const failed: string[] = [];
    for (const d of selected) {
      const dr = draftOf(d);
      try {
        await apiFetch("/api/v1/ping/targets", {
          method: "POST",
          body: JSON.stringify({ url: d.url, cluster: d.cluster, display_name: dr.name.trim() || null, group_kind: dr.group }),
        });
      } catch (e) {
        failed.push(`${d.url}: ${getErrorMessage(e)}`);
      }
    }
    setSaving(false);
    onAdded();
    if (failed.length) setErr(failed.join(" · "));
    else onClose();
  }

  return (
    <PingModal title="Add ping target" maxWidth="max-w-xl" onClose={onClose}>
      <p className="text-xs text-muted-foreground mb-3">
        Detected from <span className="font-mono">measurement=ping</span> in the last 15 minutes, not yet monitored.
      </p>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search url or name…"
          className="px-2.5 py-1 text-xs rounded-md border border-border bg-background w-56"
        />
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          {(["all", "dc", "drc"] as const).map((s) => (
            <button key={s} onClick={() => setSite(s)}
              className={`px-3 py-1 transition ${site === s ? "bg-foreground text-background" : "hover:bg-muted/50"}`}>
              {s === "all" ? "All" : s.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-md border border-border max-h-[50vh] overflow-y-auto divide-y divide-border">
        {isLoading ? (
          <p className="text-sm text-muted-foreground py-6 text-center">Loading detected targets…</p>
        ) : error ? (
          <p className="text-sm text-red-500 py-6 text-center">Couldn&apos;t load detected targets.</p>
        ) : found.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">All detected targets are already monitored.</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">No targets match.</p>
        ) : (
          rows.map((d) => {
            const dr = draftOf(d);
            return (
              <div key={keyOf(d)} className="flex flex-wrap items-center gap-2 px-3 py-2">
                <label className="flex items-center gap-2 min-w-0 flex-1 cursor-pointer">
                  <input type="checkbox" checked={dr.checked} onChange={(e) => setDraft(d, { checked: e.target.checked })} />
                  <span className="min-w-0">
                    <span className="block font-mono text-sm truncate">{d.url}</span>
                    <span className="block text-xs text-muted-foreground">
                      {d.cluster.toUpperCase()} · seen {fmtAge(d.last_seen_ms)} ago
                    </span>
                  </span>
                </label>
                <input
                  value={dr.name}
                  disabled={!dr.checked}
                  onChange={(e) => setDraft(d, { name: e.target.value })}
                  aria-label="Display name"
                  placeholder="Display name"
                  className="px-2 py-1 text-xs rounded-md border border-border bg-background w-40 disabled:opacity-50"
                />
                <select
                  value={dr.group}
                  disabled={!dr.checked}
                  onChange={(e) => setDraft(d, { group: e.target.value as GroupKind })}
                  aria-label="Group"
                  className="px-2 py-1 text-xs rounded-md border border-border bg-background disabled:opacity-50"
                >
                  {GROUP_ORDER.map((g) => <option key={g} value={g}>{GROUP_LABEL[g]}</option>)}
                </select>
              </div>
            );
          })
        )}
      </div>

      {err ? <p className="text-xs text-red-500 mt-2">{err}</p> : null}
      <div className="flex items-center justify-between gap-2 mt-3">
        <span className="text-xs text-muted-foreground">{found.length} detected · {selected.length} selected</span>
        <div className="flex gap-2">
          <button onClick={onClose} className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted/50">Cancel</button>
          <button
            onClick={addSelected}
            disabled={saving || selected.length === 0}
            className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? "Adding…" : "Add selected"}
          </button>
        </div>
      </div>
    </PingModal>
  );
}
