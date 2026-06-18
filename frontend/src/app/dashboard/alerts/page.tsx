"use client";

import { useState, useEffect } from "react";
import useSWR, { mutate } from "swr";
import { swrFetcher, apiFetch, hasMinRole } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AlertRule } from "@/types";

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 border-red-300",
  WARNING: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 border-amber-300",
  INFO: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400 border-blue-300",
};

const DATA_SOURCES = [
  { value: "appid_flow", label: "AppID Flow" },
  { value: "sdwan_sla", label: "SD-WAN SLA" },
  { value: "ha_resource", label: "HA Resource" },
  { value: "vpn_ssl", label: "SSL VPN" },
  { value: "vpn_ipsec", label: "IPsec VPN" },
];

const AGGREGATIONS = ["avg", "max", "min", "sum", "count"];
const CONDITIONS = [">", "<", ">=", "<=", "=="];
const SEVERITIES = ["INFO", "WARNING", "CRITICAL"];
const CHANNELS = ["telegram", "email"];

interface RuleForm {
  name: string;
  severity: string;
  data_source: string;
  metric_field: string;
  aggregation: string;
  condition: string;
  threshold_value: number;
  evaluation_window_minutes: number;
  sustained_for_minutes: number;
  notify_channels: string[];
  enabled: boolean;
}

const emptyForm: RuleForm = {
  name: "",
  severity: "WARNING",
  data_source: "ha_resource",
  metric_field: "ha_member.cpu_usage",
  aggregation: "avg",
  condition: ">",
  threshold_value: 80,
  evaluation_window_minutes: 5,
  sustained_for_minutes: 2,
  notify_channels: ["telegram"],
  enabled: true,
};

export default function AlertsPage() {
  const [showModal, setShowModal] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);
  const [form, setForm] = useState<RuleForm>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<{
    current_metric_value: number;
    threshold_breached: boolean;
    query_took_ms: number;
  } | null>(null);
  const canManageAlerts = hasMinRole("admin");
  const [testing, setTesting] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [historyTab, setHistoryTab] = useState<string | null>(null);

  const { data: rulesData, error, isLoading } = useSWR<{ data: AlertRule[] }>(
    "/api/v1/alerts/rules",
    swrFetcher,
    { refreshInterval: 30000 }
  );
  const rules = rulesData?.data || [];

  const { data: logsData } = useSWR<{ data: { id: string; rule_name: string; severity: string; metric_value_at_firing: number; fired_at: string; resolved_at: string | null }[] }>(
    showHistory ? "/api/v1/alerts/logs?limit=50" : null,
    swrFetcher
  );
  const alertLogs = logsData?.data || [];

  function openCreate() {
    setEditingRule(null);
    setForm(emptyForm);
    setTestResult(null);
    setShowModal(true);
  }

  function openEdit(rule: AlertRule) {
    setEditingRule(rule);
    setForm({
      name: rule.name,
      severity: rule.severity,
      data_source: rule.data_source,
      metric_field: rule.metric_field,
      aggregation: rule.aggregation,
      condition: rule.condition,
      threshold_value: rule.threshold_value,
      evaluation_window_minutes: rule.evaluation_window_minutes,
      sustained_for_minutes: rule.sustained_for_minutes,
      notify_channels: rule.notify_channels,
      enabled: rule.enabled,
    });
    setTestResult(null);
    setShowModal(true);
  }

  async function saveRule() {
    setSaving(true);
    try {
      if (editingRule) {
        await apiFetch(`/api/v1/alerts/rules/${editingRule.id}`, {
          method: "PUT",
          body: JSON.stringify(form),
        });
      } else {
        await apiFetch("/api/v1/alerts/rules", {
          method: "POST",
          body: JSON.stringify(form),
        });
      }
      setShowModal(false);
      mutate("/api/v1/alerts/rules");
    } catch (e: unknown) {
      alert(`Failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  async function deleteRule(id: string) {
    if (!confirm("Delete this alert rule?")) return;
    try {
      await apiFetch(`/api/v1/alerts/rules/${id}`, { method: "DELETE" });
      mutate("/api/v1/alerts/rules");
    } catch (e: unknown) {
      alert(`Failed: ${(e as Error).message}`);
    }
  }

  async function toggleRule(rule: AlertRule) {
    try {
      await apiFetch(`/api/v1/alerts/rules/${rule.id}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !rule.enabled }),
      });
      mutate("/api/v1/alerts/rules");
    } catch (e: unknown) {
      alert(`Failed: ${(e as Error).message}`);
    }
  }

  async function testRule(rule: AlertRule) {
    setTesting(true);
    try {
      const resp = await apiFetch<{ data: { current_metric_value: number; threshold_breached: boolean; query_took_ms: number } }>(
        `/api/v1/alerts/rules/${rule.id}/test`,
        { method: "POST" }
      );
      setTestResult(resp.data || null);
    } catch (e: unknown) {
      alert(`Test failed: ${(e as Error).message}`);
    } finally {
      setTesting(false);
    }
  }

  function toggleChannel(ch: string) {
    setForm((prev) => ({
      ...prev,
      notify_channels: prev.notify_channels.includes(ch)
        ? prev.notify_channels.filter((c) => c !== ch)
        : [...prev.notify_channels, ch],
    }));
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Alert Rules</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="px-3 py-1.5 text-xs rounded-md border bg-background hover:bg-muted transition-colors"
          >
            {showHistory ? "Hide History" : "Alert History"}
          </button>
          {canManageAlerts && (
          <button
            onClick={openCreate}
            className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            + Create Rule
          </button>
          )}
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
          Failed to load alert rules.{" "}
          <button onClick={() => mutate("/api/v1/alerts/rules")} className="underline">Retry</button>
        </div>
      )}

      {/* Alert History Panel */}
      {showHistory && (
        <div className="bg-card border rounded-lg p-4">
          <h3 className="text-sm font-semibold mb-3">Alert Firing History</h3>
          {alertLogs.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-6">No alert history</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 px-2">Rule</th>
                    <th className="text-left py-2 px-2">Severity</th>
                    <th className="text-right py-2 px-2">Value at Fire</th>
                    <th className="text-left py-2 px-2">Fired At</th>
                    <th className="text-left py-2 px-2">Resolved At</th>
                  </tr>
                </thead>
                <tbody>
                  {alertLogs.map((log) => (
                    <tr key={log.id} className="border-b last:border-0">
                      <td className="py-2 px-2 font-medium">{log.rule_name}</td>
                      <td className="py-2 px-2">
                        <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-medium border", SEVERITY_COLORS[log.severity] || "")}>
                          {log.severity}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right font-mono">{log.metric_value_at_firing.toFixed(2)}</td>
                      <td className="py-2 px-2 text-[10px]">{new Date(log.fired_at).toLocaleString()}</td>
                      <td className="py-2 px-2 text-[10px] text-muted-foreground">
                        {log.resolved_at ? new Date(log.resolved_at).toLocaleString() : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Rules Table */}
      <div className="bg-card border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-muted-foreground">
                <th className="text-left py-2.5 px-3 font-medium">Rule Name</th>
                <th className="text-left py-2.5 px-3 font-medium">Severity</th>
                <th className="text-left py-2.5 px-3 font-medium">Source</th>
                <th className="text-left py-2.5 px-3 font-medium">Metric</th>
                <th className="text-center py-2.5 px-3 font-medium">Condition</th>
                <th className="text-center py-2.5 px-3 font-medium">Enabled</th>
                <th className="text-right py-2.5 px-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b animate-pulse">
                    {Array.from({ length: 7 }).map((_, j) => (
                      <td key={j} className="py-3 px-3"><div className="h-4 bg-muted rounded" /></td>
                    ))}
                  </tr>
                ))
              ) : rules.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-muted-foreground">
                    No alert rules configured. Create your first rule to get started.
                  </td>
                </tr>
              ) : (
                rules.map((rule) => (
                  <tr key={rule.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                    <td className="py-2.5 px-3 font-medium">{rule.name}</td>
                    <td className="py-2.5 px-3">
                      <span className={cn("px-2 py-0.5 rounded-full text-[11px] font-medium border", SEVERITY_COLORS[rule.severity] || "")}>
                        {rule.severity}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-xs">{DATA_SOURCES.find((s) => s.value === rule.data_source)?.label || rule.data_source}</td>
                    <td className="py-2.5 px-3 text-xs font-mono">{rule.metric_field}</td>
                    <td className="py-2.5 px-3 text-center font-mono text-xs">
                      {rule.aggregation} {rule.condition} {rule.threshold_value}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <button
                        onClick={() => toggleRule(rule)}
                        className={cn(
                          "inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium transition-colors cursor-pointer",
                          rule.enabled
                            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                            : "bg-muted text-muted-foreground"
                        )}
                      >
                        {rule.enabled ? "ON" : "OFF"}
                      </button>
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      {canManageAlerts ? (
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => {
                            setEditingRule(rule);
                            testRule(rule);
                          }}
                          disabled={testing}
                          className="px-2 py-1 text-[11px] rounded border bg-background hover:bg-muted disabled:opacity-50"
                          title="Test Rule"
                        >
                          {testing && editingRule?.id === rule.id ? "..." : "Test"}
                        </button>
                        <button
                          onClick={() => openEdit(rule)}
                          className="px-2 py-1 text-[11px] rounded border bg-background hover:bg-muted"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => deleteRule(rule.id)}
                          className="px-2 py-1 text-[11px] rounded border border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20"
                        >
                          Del
                        </button>
                      </div>
                      ) : (
                        <span className="text-[11px] text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Test Result Preview */}
      {testResult && editingRule && (
        <div className="bg-card border rounded-lg p-4">
          <h3 className="text-sm font-semibold mb-2">
            Test Result: {editingRule.name}
          </h3>
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Current Value</p>
              <p className="text-xl font-bold">{testResult.current_metric_value.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Threshold</p>
              <p className="text-xl font-bold">
                {editingRule.condition} {editingRule.threshold_value}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Breached?</p>
              <p className={cn(
                "text-xl font-bold",
                testResult.threshold_breached ? "text-destructive" : "text-emerald-600"
              )}>
                {testResult.threshold_breached ? "YES ⚠" : "NO ✓"}
              </p>
            </div>
          </div>
          <p className="text-[10px] text-muted-foreground mt-2">
            Query took {testResult.query_took_ms}ms — No alert was fired
          </p>
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowModal(false)}>
          <div
            className="bg-card border rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-6 space-y-4">
              <h2 className="text-lg font-bold">{editingRule ? "Edit Rule" : "Create Alert Rule"}</h2>

              {/* Name */}
              <div>
                <label className="text-xs font-medium">Rule Name</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  placeholder="e.g. High CPU Alert"
                />
              </div>

              {/* Severity + Data Source */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium">Severity</label>
                  <select
                    value={form.severity}
                    onChange={(e) => setForm({ ...form, severity: e.target.value })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium">Data Source</label>
                  <select
                    value={form.data_source}
                    onChange={(e) => setForm({ ...form, data_source: e.target.value })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    {DATA_SOURCES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                </div>
              </div>

              {/* Metric Field */}
              <div>
                <label className="text-xs font-medium">Metric Field</label>
                <input
                  type="text"
                  value={form.metric_field}
                  onChange={(e) => setForm({ ...form, metric_field: e.target.value })}
                  className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1 font-mono"
                  placeholder="ha_member.cpu_usage"
                />
              </div>

              {/* Agg + Condition + Threshold */}
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="text-xs font-medium">Aggregation</label>
                  <select
                    value={form.aggregation}
                    onChange={(e) => setForm({ ...form, aggregation: e.target.value })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    {AGGREGATIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium">Condition</label>
                  <select
                    value={form.condition}
                    onChange={(e) => setForm({ ...form, condition: e.target.value })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    {CONDITIONS.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium">Threshold</label>
                  <input
                    type="number"
                    value={form.threshold_value}
                    onChange={(e) => setForm({ ...form, threshold_value: Number(e.target.value) })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  />
                </div>
              </div>

              {/* Window + Sustained */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium">Eval Window (min)</label>
                  <input
                    type="number"
                    value={form.evaluation_window_minutes}
                    onChange={(e) => setForm({ ...form, evaluation_window_minutes: Number(e.target.value) })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium">Sustained For (min)</label>
                  <input
                    type="number"
                    value={form.sustained_for_minutes}
                    onChange={(e) => setForm({ ...form, sustained_for_minutes: Number(e.target.value) })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  />
                </div>
              </div>

              {/* Notify Channels */}
              <div>
                <label className="text-xs font-medium">Notify Channels</label>
                <div className="flex gap-2 mt-1">
                  {CHANNELS.map((ch) => (
                    <button
                      key={ch}
                      onClick={() => toggleChannel(ch)}
                      className={cn(
                        "px-3 py-1 text-xs rounded-md border transition-colors",
                        form.notify_channels.includes(ch)
                          ? "bg-primary text-primary-foreground border-primary"
                          : "bg-background text-muted-foreground border-border"
                      )}
                    >
                      {ch}
                    </button>
                  ))}
                </div>
              </div>

              {/* Enabled toggle */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                  className="rounded"
                />
                <span className="text-sm">Enabled</span>
              </label>

              {/* Actions */}
              <div className="flex justify-end gap-2 pt-3 border-t">
                <button
                  onClick={() => setShowModal(false)}
                  className="px-4 py-1.5 text-sm rounded-md border bg-background hover:bg-muted"
                >
                  Cancel
                </button>
                <button
                  onClick={saveRule}
                  disabled={saving || !form.name}
                  className="px-4 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {saving ? "Saving..." : editingRule ? "Update" : "Create"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
