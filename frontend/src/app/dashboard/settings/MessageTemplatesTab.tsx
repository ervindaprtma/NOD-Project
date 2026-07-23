"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { swrFetcher, apiFetch, getErrorMessage } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { NotificationTemplate } from "@/types";

const LIST_KEY = "/api/v1/config/notification-templates";

// The engine's render whitelist (§11.1 / _notify ctx). Shown as insertable chips.
const VARIABLES = [
  "rule.name", "rule.severity", "rule.site_name", "rule.metric_field",
  "rule.condition", "rule.threshold_value", "metric_value", "fired_at",
];

interface EditorForm {
  name: string;
  description: string;
  subject_template: string;
  body_template: string;
  line_template: string;
}

const BLANK: EditorForm = {
  name: "",
  description: "",
  subject_template: "Alert: {{ rule.name }}",
  body_template: "{{ rule.name }} {{ rule.severity }}\n{{ rule.metric_field }} {{ rule.condition }} {{ rule.threshold_value }} (now {{ metric_value }})\nFired at {{ fired_at }}",
  line_template: "",
};

export function MessageTemplatesTab({ showToast }: { showToast: (t: { ok: boolean; msg: string }) => void }) {
  const { data, isLoading } = useSWR<{ data: NotificationTemplate[] }>(LIST_KEY, swrFetcher);
  const templates = data?.data || [];

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<EditorForm>(BLANK);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<{ subject: string; body: string; line: string } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // Editable sample context for the preview.
  const [sample, setSample] = useState({ severity: "WARNING", metric_value: 95.5 });

  function selectTemplate(t: NotificationTemplate) {
    setSelectedId(t.id);
    setForm({
      name: t.name,
      description: t.description || "",
      subject_template: t.subject_template || "",
      body_template: t.body_template || "",
      line_template: t.line_template || "",
    });
    setPreview(null);
    setPreviewError(null);
  }

  function newTemplate() {
    setSelectedId(null);
    setForm(BLANK);
    setPreview(null);
    setPreviewError(null);
  }

  // Persist current form (create or update); returns the template id.
  async function save(): Promise<string> {
    if (selectedId) {
      await apiFetch(`${LIST_KEY}/${selectedId}`, { method: "PUT", body: JSON.stringify(form) });
      await mutate(LIST_KEY);
      return selectedId;
    }
    const resp = await apiFetch<{ data: { id: string } }>(LIST_KEY, {
      method: "POST",
      body: JSON.stringify(form),
    });
    const id = resp.data.id;
    setSelectedId(id);
    await mutate(LIST_KEY);
    return id;
  }

  async function onSave() {
    setSaving(true);
    try {
      await save();
      showToast({ ok: true, msg: "Template saved" });
    } catch (e) {
      showToast({ ok: false, msg: getErrorMessage(e, "Save failed") });
    } finally {
      setSaving(false);
    }
  }

  // Save first, then preview the saved template — so the render always reflects the
  // current editor state (the backend previews by id).
  async function onPreview() {
    setPreviewing(true);
    setPreviewError(null);
    try {
      const id = await save();
      const resp = await apiFetch<{ data: { subject: string; body: string; line: string } }>(
        `${LIST_KEY}/${id}/preview`,
        {
          method: "POST",
          body: JSON.stringify({
            severity: sample.severity,
            metric_value: sample.metric_value,
            site_name: "Site_FGT-DC",
          }),
        }
      );
      setPreview(resp.data);
    } catch (e) {
      // 422 → Jinja render error surfaced inline; anything else is a save/network error.
      setPreviewError(getErrorMessage(e, "Render failed"));
    } finally {
      setPreviewing(false);
    }
  }

  async function onDelete(t: NotificationTemplate) {
    if (!confirm(`Delete template "${t.name}"?`)) return;
    try {
      await apiFetch(`${LIST_KEY}/${t.id}`, { method: "DELETE" });
      await mutate(LIST_KEY);
      if (selectedId === t.id) newTemplate();
      showToast({ ok: true, msg: "Template deleted" });
    } catch (e) {
      // 409 when referenced by rules.
      showToast({ ok: false, msg: getErrorMessage(e, "Delete failed — template may be in use by rules") });
    }
  }

  function insertVar(v: string) {
    setForm((prev) => ({ ...prev, body_template: `${prev.body_template}{{ ${v} }}` }));
  }

  // Toggle active / set default operate on the saved template (via PUT), not the draft.
  async function patch(t: NotificationTemplate, fields: Record<string, unknown>, okMsg: string) {
    try {
      await apiFetch(`${LIST_KEY}/${t.id}`, { method: "PUT", body: JSON.stringify(fields) });
      await mutate(LIST_KEY);
      showToast({ ok: true, msg: okMsg });
    } catch (e) {
      showToast({ ok: false, msg: getErrorMessage(e, "Update failed") });
    }
  }

  const selected = templates.find((t) => t.id === selectedId) || null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Message Templates</h2>
          <p className="text-xs text-muted-foreground">Notification text rendered when a rule fires (sandboxed Jinja2).</p>
        </div>
        <button
          onClick={newTemplate}
          className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
        >
          + New template
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-4">
        {/* List */}
        <div className="border rounded-lg divide-y max-h-[420px] overflow-y-auto">
          {isLoading ? (
            <div className="p-3 text-xs text-muted-foreground">Loading…</div>
          ) : templates.length === 0 ? (
            <div className="p-3 text-xs text-muted-foreground">No templates yet.</div>
          ) : (
            templates.map((t) => (
              <button
                key={t.id}
                onClick={() => selectTemplate(t)}
                className={cn(
                  "w-full text-left px-3 py-2 hover:bg-muted/50 transition-colors",
                  selectedId === t.id && "bg-muted"
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span className={cn("text-sm font-medium truncate", !t.is_active && "text-muted-foreground line-through")}>{t.name}</span>
                  {t.is_default && <span className="text-[9px] px-1 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">default</span>}
                  {!t.is_active && <span className="text-[9px] px-1 rounded bg-muted text-muted-foreground">inactive</span>}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {t.used_by_count ? `used by ${t.used_by_count} rule(s)` : "unused"}
                </div>
              </button>
            ))
          )}
        </div>

        {/* Editor + preview */}
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium">Name</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                placeholder="Critical — Telegram"
              />
            </div>
            <div>
              <label className="text-xs font-medium">Description</label>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium">Subject</label>
            <input
              value={form.subject_template}
              onChange={(e) => setForm({ ...form, subject_template: e.target.value })}
              className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1 font-mono"
            />
          </div>

          <div>
            <label className="text-xs font-medium">Body</label>
            <textarea
              value={form.body_template}
              onChange={(e) => setForm({ ...form, body_template: e.target.value })}
              rows={5}
              className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1 font-mono"
            />
          </div>

          <div>
            <label className="text-xs font-medium">Line (optional, for batched notifiers)</label>
            <input
              value={form.line_template}
              onChange={(e) => setForm({ ...form, line_template: e.target.value })}
              className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1 font-mono"
            />
          </div>

          {/* Variable chips */}
          <div className="flex flex-wrap gap-1">
            <span className="text-[10px] text-muted-foreground self-center mr-1">Insert:</span>
            {VARIABLES.map((v) => (
              <button
                key={v}
                onClick={() => insertVar(v)}
                className="px-1.5 py-0.5 text-[10px] rounded border bg-background hover:bg-muted font-mono"
                title={`Append {{ ${v} }} to the body`}
              >
                {v}
              </button>
            ))}
          </div>

          {/* Sample data + actions */}
          <div className="flex flex-wrap items-end gap-2 pt-2 border-t">
            <div>
              <label className="text-[10px] text-muted-foreground">Sample severity</label>
              <select
                value={sample.severity}
                onChange={(e) => setSample({ ...sample, severity: e.target.value })}
                className="block px-2 py-1 text-xs rounded border bg-background mt-0.5"
              >
                {["INFO", "WARNING", "CRITICAL"].map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-muted-foreground">Sample value</label>
              <input
                type="number"
                value={sample.metric_value}
                onChange={(e) => setSample({ ...sample, metric_value: Number(e.target.value) })}
                className="block w-24 px-2 py-1 text-xs rounded border bg-background mt-0.5"
              />
            </div>
            <div className="flex-1" />
            <button
              onClick={onPreview}
              disabled={previewing || !form.body_template || !form.name}
              className="px-3 py-1.5 text-xs rounded-md border bg-background hover:bg-muted disabled:opacity-50"
            >
              {previewing ? "…" : "Save & Preview"}
            </button>
            <button
              onClick={onSave}
              disabled={saving || !form.body_template || !form.name}
              className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? "Saving…" : selectedId ? "Save" : "Create"}
            </button>
            {selected && (
              <button
                onClick={() => onDelete(selected)}
                className="px-3 py-1.5 text-xs rounded-md border border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20"
              >
                Delete
              </button>
            )}
          </div>

          {/* Lifecycle actions on the saved template */}
          {selected && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted-foreground">Status:</span>
              <button
                onClick={() => patch(selected, { is_active: !selected.is_active }, selected.is_active ? "Template deactivated" : "Template activated")}
                className={cn(
                  "px-2.5 py-1 rounded-md border transition-colors",
                  selected.is_active
                    ? "bg-emerald-50 border-emerald-200 text-emerald-700 dark:bg-emerald-950/20 dark:border-emerald-900 dark:text-emerald-400"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {selected.is_active ? "● Active — click to deactivate" : "○ Inactive — click to activate"}
              </button>
              {selected.is_default ? (
                <span className="px-2.5 py-1 rounded-md bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">★ Default template</span>
              ) : (
                <button
                  onClick={() => patch(selected, { is_default: true }, `"${selected.name}" is now the default`)}
                  className="px-2.5 py-1 rounded-md border bg-background hover:bg-muted"
                  title="Rules with no template assigned will use this one"
                >
                  Set as default
                </button>
              )}
            </div>
          )}

          {/* Preview output / 422 */}
          {previewError && (
            <div className="p-3 rounded-md bg-red-50 border border-red-200 text-red-700 dark:bg-red-950/20 dark:border-red-900 dark:text-red-400 text-xs">
              {previewError}
            </div>
          )}
          {preview && !previewError && (
            <div className="p-3 rounded-md border bg-muted/30 text-xs space-y-2">
              <div><span className="text-muted-foreground">Subject:</span> <span className="font-medium">{preview.subject}</span></div>
              <div>
                <span className="text-muted-foreground">Body:</span>
                <pre className="whitespace-pre-wrap font-mono mt-1">{preview.body}</pre>
              </div>
              {preview.line && (
                <div><span className="text-muted-foreground">Line:</span> <span className="font-mono">{preview.line}</span></div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
