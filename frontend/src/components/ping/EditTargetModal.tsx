"use client";

import { useState } from "react";
import { apiFetch, getErrorMessage } from "@/lib/api";
import type { GroupKind, PingTarget } from "./types";
import { GROUP_LABEL, GROUP_ORDER } from "./types";
import { PingModal } from "./PingModal";

const FIELD = "w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1";

// Edit-target modal (page design §5). url + cluster are the identity → read-only.
export function EditTargetModal({ target, onClose, onSaved, onDeleted }: {
  target: PingTarget;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(target.custom_name ?? "");
  const [group, setGroup] = useState<GroupKind>(target.group_kind);
  const [expectedDown, setExpectedDown] = useState(target.expected_down);
  const [enabled, setEnabled] = useState(target.enabled);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await apiFetch(`/api/v1/ping/targets/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify({ display_name: name, group_kind: group, expected_down: expectedDown, enabled }),
      });
      onSaved();
      onClose();
    } catch (e) {
      setErr(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm(`Delete ping target "${target.display_name}"?`)) return;
    setBusy(true);
    setErr(null);
    try {
      await apiFetch(`/api/v1/ping/targets/${target.id}`, { method: "DELETE" });
      onDeleted();
    } catch (e) {
      setErr(getErrorMessage(e));
      setBusy(false);
    }
  }

  return (
    <PingModal title="Edit ping target" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground font-mono">{target.url} · {target.cluster.toUpperCase()}</p>
        <div>
          <label className="text-xs font-medium">Display name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={target.display_name} className={FIELD} />
          <p className="text-[10px] text-muted-foreground mt-1">Leave blank to use the collector&apos;s name or the url.</p>
        </div>
        <div>
          <label className="text-xs font-medium">Group</label>
          <select value={group} onChange={(e) => setGroup(e.target.value as GroupKind)} className={FIELD}>
            {GROUP_ORDER.map((g) => <option key={g} value={g}>{GROUP_LABEL[g]}</option>)}
          </select>
        </div>
        <label className="flex items-center gap-2 cursor-pointer text-sm">
          <input type="checkbox" checked={expectedDown} onChange={(e) => setExpectedDown(e.target.checked)} />
          Expected down <span className="text-xs text-muted-foreground">(DR link — excluded from incident counts)</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        {err ? <p className="text-xs text-red-500">{err}</p> : null}
        <div className="flex items-center justify-between pt-1">
          <button onClick={remove} disabled={busy} className="text-xs text-red-600 dark:text-red-400 hover:underline disabled:opacity-50">
            Delete
          </button>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted/50">Cancel</button>
            <button onClick={save} disabled={busy}
              className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </PingModal>
  );
}
