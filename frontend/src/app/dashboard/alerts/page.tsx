"use client";

import { useState, useEffect, useRef } from "react";
import useSWR, { mutate } from "swr";
import { swrFetcher, apiFetch, hasMinRole } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AlertRule, AlertFieldCatalog, AlertEngineHealth, NotificationTemplate } from "@/types";

// Compact relative time: "12s ago", "4m ago", "2h ago". Empty for null.
function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// Countdown to a future ISO time: "in 52s" / "in 3m". Empty for past/null.
function timeUntil(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  if (s <= 0) return "due";
  if (s < 60) return `in ${s}s`;
  return `in ${Math.floor(s / 60)}m`;
}

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 border-red-300",
  WARNING: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 border-amber-300",
  INFO: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400 border-blue-300",
};

// Live state-machine chip (semantic, distinct from severity accent).
const STATE_STYLE: Record<string, { dot: string; text: string; label: string }> = {
  FIRING: { dot: "bg-red-500 animate-pulse", text: "text-red-700 dark:text-red-400", label: "FIRING" },
  PENDING: { dot: "bg-amber-500", text: "text-amber-700 dark:text-amber-400", label: "PENDING" },
  RESOLVED: { dot: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-400", label: "RESOLVED" },
  INACTIVE: { dot: "bg-muted-foreground", text: "text-muted-foreground", label: "OK" },
};

const DATA_SOURCES = [
  { value: "appid_flow", label: "AppID Flow" },
  { value: "sdwan_sla", label: "SD-WAN SLA" },
  { value: "ha_resource", label: "HA Resource" },
  { value: "vpn_ssl", label: "SSL VPN" },
  { value: "vpn_ipsec", label: "IPsec VPN" },
  { value: "interface_stats", label: "Interface Bandwidth" },
  { value: "device_uptime", label: "Device Availability" },
];

// interface_stats requires a canonical site (the interface picker keys off it) and a
// window ≥ 2 min (the counter→rate derivative needs 2 histogram buckets).
const SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"];
const IFACE_MIN_WINDOW = 2;
// device_uptime: ≥5 min so a dropped 30s poll or two can't trip a false "down" (§11.2).
const DEVICE_MIN_WINDOW = 5;

// Controlled numeric input that keeps its own string state so it can be cleared/edited
// freely (empty, mid-typing, leading zeros) instead of a plain `value={number}` that snaps
// back to a forced 0 and can't be emptied. Emits the parsed number; empty → 0. Adopts an
// external value change (template apply, %-of-max compute, edit) via a last-emitted ref so
// it doesn't fight the user while typing.
function NumberField({
  value, onValueChange, className, min,
}: {
  value: number;
  onValueChange: (n: number) => void;
  className?: string;
  min?: number;
}) {
  const [raw, setRaw] = useState<string>(String(value));
  const lastEmitted = useRef<number>(value);
  useEffect(() => {
    if (value !== lastEmitted.current) {
      setRaw(String(value));
      lastEmitted.current = value;
    }
  }, [value]);
  return (
    <input
      type="number"
      inputMode="decimal"
      min={min}
      value={raw}
      onChange={(e) => {
        const v = e.target.value;
        setRaw(v);
        const n = v === "" ? 0 : Number(v);
        if (!Number.isNaN(n)) {
          lastEmitted.current = n;
          onValueChange(n);
        }
      }}
      className={className}
    />
  );
}

const AGGREGATIONS = ["avg", "max", "min", "sum", "count"];
const CONDITIONS = [">", "<", ">=", "<=", "=="];
const SEVERITIES = ["INFO", "WARNING", "CRITICAL"];
const CHANNELS = ["whatsapp", "telegram", "smtp", "discord"];

// One clause of a composite rule. Shares the rule's site + window; target_key is the
// interface ifIndex (interface_stats) or device IP (device_uptime), else blank.
interface ClauseForm {
  data_source: string;
  metric_field: string;
  aggregation: string;
  condition: string;
  threshold_value: number;
  target_key: string;
}

interface RuleForm {
  name: string;
  severity: string;
  kind: "single" | "composite";
  notify_when: "any" | "all";   // composite: Any=OR, All=AND
  clauses: ClauseForm[];
  data_source: string;
  metric_field: string;
  target_key: string;
  link_max_mbps: number | null;   // interface throughput: set → "% of link max" mode
  aggregation: string;
  condition: string;
  threshold_value: number;
  evaluation_window_minutes: number;
  sustained_for_minutes: number;
  notify_channels: string[];
  notification_template_id: string;
  site_name: string;
  enabled: boolean;
}

const emptyForm: RuleForm = {
  name: "",
  severity: "WARNING",
  kind: "single",
  notify_when: "any",
  clauses: [],
  data_source: "ha_resource",
  metric_field: "ha_member.cpu_usage",
  target_key: "",
  link_max_mbps: null,
  aggregation: "avg",
  condition: ">",
  threshold_value: 80,
  evaluation_window_minutes: 5,
  sustained_for_minutes: 2,
  notify_channels: ["telegram"],
  notification_template_id: "",
  site_name: "Site_FGT-DC",
  enabled: true,
};

// Minimal shape of a rule template from GET /api/v1/alerts/templates. locked_fields
// keys map 1:1 to RuleForm fields (single-rule templates); composite templates carry
// `clauses` instead, so those keys are simply absent and the form keeps its defaults.
type AlertTemplateLite = {
  id: string;
  name: string;
  icon: string;
  category: string;
  locked_fields: Record<string, string | number | undefined>;
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

  // Phase C: engine-health status line (refreshes on its own cadence).
  const { data: healthData } = useSWR<{ data: AlertEngineHealth }>(
    canManageAlerts ? "/api/v1/alerts/engine-health" : null,
    swrFetcher,
    { refreshInterval: 15000 }
  );
  const health = healthData?.data;

  // Phase A: catalog drives the metric/aggregation/condition choices — no free text.
  // Fetched per data source only while the modal is open (SWR caches across opens).
  const { data: catalogData } = useSWR<{ data: AlertFieldCatalog[] }>(
    showModal ? `/api/v1/alerts/fields?data_source=${form.data_source}` : null,
    swrFetcher
  );
  const fields = catalogData?.data || [];
  const selectedField = fields.find((f) => f.field_key === form.metric_field) || null;

  const isComposite = form.kind === "composite";
  // Full catalog (all sources) for the composite clause editor — one call, filtered client-side.
  const { data: allFieldsData } = useSWR<{ data: AlertFieldCatalog[] }>(
    showModal && isComposite ? "/api/v1/alerts/fields" : null,
    swrFetcher
  );
  const catalogAll = allFieldsData?.data || [];
  const fieldsForSource = (ds: string) => catalogAll.filter((f) => f.data_source === ds);

  // Message templates for the assignment dropdown (§11.1). Loaded while the modal is open.
  const { data: templatesData } = useSWR<{ data: NotificationTemplate[] }>(
    showModal ? "/api/v1/config/notification-templates" : null,
    swrFetcher
  );
  const messageTemplates = templatesData?.data || [];

  // Rule-template gallery (v3 §3.12) — fetched from the backend seeder, not hardcoded,
  // so new templates (e.g. Device Availability Uptime) appear without a frontend change.
  const { data: ruleTemplatesData } = useSWR<{ data: AlertTemplateLite[] }>(
    "/api/v1/alerts/templates",
    swrFetcher
  );
  const ruleTemplates = ruleTemplatesData?.data || [];
  // Constrain agg/condition to the chosen field; fall back to full lists pre-load.
  const metricAggs = selectedField?.valid_aggregations?.length ? selectedField.valid_aggregations : AGGREGATIONS;
  const metricConds = selectedField?.valid_conditions?.length ? selectedField.valid_conditions : CONDITIONS;

  // Apply a catalog field to the form: set metric, snap agg/cond into its valid
  // sets, and prefill the threshold from the catalog example.
  function applyField(f: AlertFieldCatalog) {
    setForm((prev) => ({
      ...prev,
      metric_field: f.field_key,
      aggregation: f.valid_aggregations?.includes(prev.aggregation) ? prev.aggregation : (f.valid_aggregations?.[0] || prev.aggregation),
      condition: f.valid_conditions?.includes(prev.condition) ? prev.condition : (f.valid_conditions?.[0] || prev.condition),
      threshold_value: f.example_threshold ?? prev.threshold_value,
    }));
  }

  // ── Composite clause editor helpers ──────────────────────────────
  function makeClause(ds: string): ClauseForm {
    const f = fieldsForSource(ds)[0];
    return {
      data_source: ds,
      metric_field: f?.field_key || "",
      aggregation: f?.valid_aggregations?.[0] || "avg",
      condition: f?.valid_conditions?.[0] || ">",
      threshold_value: f?.example_threshold ?? 0,
      target_key: "",
    };
  }
  const updateClauses = (fn: (cs: ClauseForm[]) => ClauseForm[]) =>
    setForm((prev) => ({ ...prev, clauses: fn(prev.clauses) }));
  const addClause = () => updateClauses((cs) => [...cs, makeClause("sdwan_sla")]);
  const removeClause = (i: number) => updateClauses((cs) => cs.filter((_, idx) => idx !== i));
  const patchClause = (i: number, patch: Partial<ClauseForm>) =>
    updateClauses((cs) => cs.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  // Changing a clause's source resets its metric/agg/condition to that source's first field.
  function setClauseSource(i: number, ds: string) {
    const f = fieldsForSource(ds)[0];
    patchClause(i, {
      data_source: ds, metric_field: f?.field_key || "", target_key: "",
      aggregation: f?.valid_aggregations?.[0] || "avg",
      condition: f?.valid_conditions?.[0] || ">",
      threshold_value: f?.example_threshold ?? 0,
    });
  }
  function setClauseMetric(i: number, key: string) {
    const c = form.clauses[i];
    const f = fieldsForSource(c.data_source).find((x) => x.field_key === key);
    patchClause(i, {
      metric_field: key,
      aggregation: f?.valid_aggregations?.includes(c.aggregation) ? c.aggregation : (f?.valid_aggregations?.[0] || "avg"),
      condition: f?.valid_conditions?.includes(c.condition) ? c.condition : (f?.valid_conditions?.[0] || ">"),
      threshold_value: f?.example_threshold ?? c.threshold_value,
    });
  }

  // A clause added before the full catalog loaded has an empty metric_field — fill it
  // from the first field of its source once the catalog arrives.
  useEffect(() => {
    if (!isComposite || catalogAll.length === 0) return;
    if (!form.clauses.some((c) => !c.metric_field)) return;
    setForm((prev) => ({
      ...prev,
      clauses: prev.clauses.map((c) => {
        if (c.metric_field) return c;
        const f = catalogAll.find((x) => x.data_source === c.data_source);
        return f
          ? { ...c, metric_field: f.field_key, aggregation: f.valid_aggregations?.[0] || c.aggregation,
              condition: f.valid_conditions?.[0] || c.condition, threshold_value: f.example_threshold ?? c.threshold_value }
          : c;
      }),
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isComposite, catalogAll, form.clauses]);

  // When the data source changes (or catalog first loads), if the current metric
  // isn't offered by this source, snap to the first cataloged field.
  useEffect(() => {
    if (!showModal || fields.length === 0) return;
    if (!fields.some((f) => f.field_key === form.metric_field)) {
      applyField(fields[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fields, form.metric_field, showModal]);

  // Phase E: interface_stats needs a canonical site + an interface (target_key). Composite
  // clauses may also target interfaces/devices, so fetch the site's lists whenever composite.
  const isIface = form.data_source === "interface_stats";
  const { data: ifaceData } = useSWR<{ data: { key: string; label: string }[] }>(
    showModal && (isIface || isComposite) && SITES.includes(form.site_name)
      ? `/api/v1/alerts/interfaces?site_name=${form.site_name}`
      : null,
    swrFetcher
  );
  const interfaces = ifaceData?.data || [];

  // For interface_stats: force a canonical site, and default target_key to the first
  // interface once the list loads (or when the current one isn't at this site).
  useEffect(() => {
    if (!showModal || !isIface) return;
    if (!SITES.includes(form.site_name)) {
      setForm((prev) => ({ ...prev, site_name: SITES[0] }));
      return;
    }
    if (interfaces.length && !interfaces.some((i) => i.key === form.target_key)) {
      setForm((prev) => ({ ...prev, target_key: interfaces[0].key }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isIface, interfaces, form.site_name, form.target_key, showModal]);

  const ifaceWindowTooShort = isIface && form.evaluation_window_minutes < IFACE_MIN_WINDOW;
  // The backend requires eval window ≥ 1 (a cleared/0 field would 422 on save).
  const windowInvalid = !(form.evaluation_window_minutes >= 1);

  // Track AL: device_uptime needs a canonical site (device picker keys off it). Unlike
  // interfaces the device is OPTIONAL — blank = "any device at the site", and collector_gap
  // is site-level so it must stay blank.
  const isDevice = form.data_source === "device_uptime";
  const isSiteLevelMetric = isDevice && form.metric_field === "collector_gap";
  const { data: deviceData } = useSWR<{ data: { key: string; label: string }[] }>(
    showModal && (isDevice || isComposite) && SITES.includes(form.site_name)
      ? `/api/v1/alerts/devices?site_name=${form.site_name}`
      : null,
    swrFetcher
  );
  const devices = deviceData?.data || [];

  useEffect(() => {
    if (!showModal || !isDevice) return;
    if (!SITES.includes(form.site_name)) {
      setForm((prev) => ({ ...prev, site_name: SITES[0] }));
    }
    // collector_gap is site-level: never carry a device on it.
    if (isSiteLevelMetric && form.target_key) {
      setForm((prev) => ({ ...prev, target_key: "" }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDevice, isSiteLevelMetric, form.site_name, form.target_key, showModal]);

  const deviceWindowTooShort = isDevice && form.evaluation_window_minutes < DEVICE_MIN_WINDOW;

  // SD-WAN: pick the link (→ target_key = 1-based link number), site-aware named list
  // (WAN uplink or IPsec/ADVPN tunnel). Composite clauses may target links too.
  const isSdwan = form.data_source === "sdwan_sla";
  const { data: sdwanLinksData } = useSWR<{ data: { key: string; label: string; type: string }[] }>(
    showModal && (isSdwan || isComposite) && SITES.includes(form.site_name)
      ? `/api/v1/alerts/sdwan-links?site_name=${form.site_name}`
      : null,
    swrFetcher
  );
  const sdwanLinks = sdwanLinksData?.data || [];

  useEffect(() => {
    if (!showModal || !isSdwan) return;
    if (sdwanLinks.length && !sdwanLinks.some((l) => l.key === form.target_key)) {
      setForm((prev) => ({ ...prev, target_key: sdwanLinks[0].key }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSdwan, sdwanLinks, form.target_key, showModal]);

  // Composite is valid only with ≥2 fully-specified clauses; an interface clause needs its
  // ifIndex or the engine can't resolve which interface (→ reads 0).
  const compositeInvalid =
    isComposite &&
    (form.clauses.length < 2 ||
      form.clauses.some(
        (c) => !c.metric_field ||
          ((c.data_source === "interface_stats" || c.data_source === "sdwan_sla") && !c.target_key)
      ));

  // SD-WAN link Up/Down: metric status_linkN, 0=Up / >=1=Down. A Down/Up toggle drives
  // condition+threshold instead of raw numeric inputs.
  const isSdwanStatus = isSdwan && form.metric_field === "status";
  const sdwanWantsUp = form.condition === "==" && form.threshold_value === 0;
  // Interface throughput: absolute Mbps, or % of an operator-entered link max (link_max_mbps
  // set → % mode; threshold_value stays Mbps = max × %).
  const isThroughput = isIface && form.metric_field === "iface.throughput_mbps";
  const thrIsPct = form.link_max_mbps != null;
  const thrPct = thrIsPct && form.link_max_mbps ? Math.round((form.threshold_value / form.link_max_mbps) * 100) : 90;

  const { data: logsData } = useSWR<{ data: { id: string; rule_name: string; severity: string; metric_value_at_firing: number; fired_at: string; resolved_at: string | null }[] }>(
    showHistory ? "/api/v1/alerts/logs?limit=50" : null,
    swrFetcher
  );
  const alertLogs = logsData?.data || [];

  // §9.8: SSE live push. EventSource cannot set custom headers, so we
  // request a short-lived stream token (POST /stream-token) and pass it
  // as a query param. The 30s useSWR poll stays as a reconciliation
  // fallback — SSE reconnect gaps are common.
  const [liveState, setLiveState] = useState<"connecting" | "open" | "closed">("closed");
  useEffect(() => {
    if (!canManageAlerts) return;  // viewer doesn't need live updates
    let es: EventSource | null = null;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    async function connect() {
      try {
        const tokResp = await apiFetch<{ data: { token: string } }>(
          "/api/v1/alerts/stream-token",
          { method: "POST" }
        );
        if (cancelled) return;
        const token = tokResp.data.token;
        setLiveState("connecting");
        es = new EventSource(`/api/v1/alerts/stream?token=${encodeURIComponent(token)}`);
        es.onopen = () => setLiveState("open");
        es.onerror = () => {
          setLiveState("closed");
          es?.close();
          if (!cancelled) retryTimer = setTimeout(connect, 5000);
        };
        es.addEventListener("alert", () => {
          // ponytail: invalidate both caches — the rule's last_fired_at may
          // have changed too, and the history table wants the new row.
          mutate("/api/v1/alerts/rules");
          mutate((key: string) => typeof key === "string" && key.startsWith("/api/v1/alerts/logs"));
        });
        es.addEventListener("resolved", () => {
          mutate("/api/v1/alerts/rules");
          mutate((key: string) => typeof key === "string" && key.startsWith("/api/v1/alerts/logs"));
        });
      } catch {
        setLiveState("closed");
        if (!cancelled) retryTimer = setTimeout(connect, 10000);
      }
    }
    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
      setLiveState("closed");
    };
  }, [canManageAlerts]);

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
      kind: rule.kind || "single",
      notify_when: rule.notify_when || "any",
      clauses: (rule.clauses || []).map((c) => ({
        data_source: String(c.data_source ?? ""),
        metric_field: String(c.metric_field ?? ""),
        aggregation: String(c.aggregation ?? "avg"),
        condition: String(c.condition ?? ">"),
        threshold_value: Number(c.threshold_value ?? 0),
        target_key: c.target_key ? String(c.target_key) : "",
      })),
      data_source: rule.data_source,
      metric_field: rule.metric_field,
      target_key: rule.target_key || "",
      link_max_mbps: rule.link_max_mbps ?? null,
      aggregation: rule.aggregation,
      condition: rule.condition,
      threshold_value: rule.threshold_value,
      evaluation_window_minutes: rule.evaluation_window_minutes,
      sustained_for_minutes: rule.sustained_for_minutes,
      notify_channels: rule.notify_channels,
      notification_template_id: rule.notification_template_id || "",
      site_name: rule.site_name || "",
      enabled: rule.enabled,
    });
    setTestResult(null);
    setShowModal(true);
  }

  async function saveRule() {
    setSaving(true);
    // target_key applies to interface_stats (required) and device_uptime (optional; blank =
    // any device / site-level). Cleared for every other source. Empty string → null.
    const keepsTargetKey = form.data_source === "interface_stats" || form.data_source === "device_uptime" || form.data_source === "sdwan_sla";
    // link_max_mbps only rides along with interface throughput's %-of-max mode.
    const keepsLinkMax = form.data_source === "interface_stats" && form.metric_field === "iface.throughput_mbps";

    // Composite: clauses drive evaluation. The DB still requires the top-level metric
    // columns (NOT NULL), so mirror clause[0] into them — the engine ignores them for a
    // composite rule but the list view shows something sensible. Clean each clause's
    // target_key (only interface/device carry one).
    const cleanClauses = form.clauses.map((c) => ({
      ...c,
      target_key: (c.data_source === "interface_stats" || c.data_source === "device_uptime" || c.data_source === "sdwan_sla")
        ? (c.target_key || null) : null,
    }));
    const c0 = form.clauses[0];
    const composite = form.kind === "composite" && c0
      ? { data_source: c0.data_source, metric_field: c0.metric_field, aggregation: c0.aggregation,
          condition: c0.condition, threshold_value: c0.threshold_value, target_key: null, link_max_mbps: null }
      : {};

    const payload = {
      ...form,
      target_key: keepsTargetKey ? (form.target_key || null) : null,
      link_max_mbps: keepsLinkMax ? form.link_max_mbps : null,
      notification_template_id: form.notification_template_id || null,
      clauses: form.kind === "composite" ? cleanClauses : [],
      ...composite,
    };
    try {
      if (editingRule) {
        await apiFetch(`/api/v1/alerts/rules/${editingRule.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await apiFetch("/api/v1/alerts/rules", {
          method: "POST",
          body: JSON.stringify(payload),
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
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight">Alert Rules</h1>
          {/* §9.8: live indicator driven by EventSource.readyState */}
          <span
            className={cn(
              "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium",
              liveState === "open"
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                : liveState === "connecting"
                  ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                  : "bg-muted text-muted-foreground"
            )}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                liveState === "open"
                  ? "bg-emerald-500 animate-pulse"
                  : liveState === "connecting"
                    ? "bg-amber-500"
                    : "bg-muted-foreground"
              )}
            />
            {liveState === "open" ? "LIVE" : liveState === "connecting" ? "CONNECTING" : "OFFLINE"}
          </span>
        </div>
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

      {/* Template Gallery (v3 §3.12) */}
      <div className="bg-card border rounded-lg p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold">Templates</h3>
          <span className="text-[10px] text-muted-foreground">Quick-start from pre-built templates</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {ruleTemplates.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                if (!canManageAlerts) return;
                const lf = t.locked_fields || {};
                setEditingRule(null);
                // Pre-fill the form from the template's locked_fields so the rule opens
                // with the correct data_source + metric + defaults already selected.
                // Only keys present are applied (composite templates keep form defaults).
                setForm({
                  ...emptyForm,
                  name: `[Template] ${t.name}`,
                  ...(lf.data_source ? { data_source: String(lf.data_source) } : {}),
                  ...(lf.metric_field ? { metric_field: String(lf.metric_field) } : {}),
                  ...(lf.aggregation ? { aggregation: String(lf.aggregation) } : {}),
                  ...(lf.condition ? { condition: String(lf.condition) } : {}),
                  ...(lf.target_key != null ? { target_key: String(lf.target_key) } : {}),
                  ...(lf.threshold_value != null ? { threshold_value: Number(lf.threshold_value) } : {}),
                  ...(lf.evaluation_window_minutes != null ? { evaluation_window_minutes: Number(lf.evaluation_window_minutes) } : {}),
                  ...(lf.sustained_for_minutes != null ? { sustained_for_minutes: Number(lf.sustained_for_minutes) } : {}),
                  ...(lf.severity ? { severity: String(lf.severity) } : {}),
                });
                setShowModal(true);
              }}
              disabled={!canManageAlerts}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border bg-background hover:bg-muted transition-colors disabled:opacity-50"
              title={t.category}
            >
              <span>{t.icon}</span>
              <span>{t.name}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Engine health status line (Phase C) */}
      {canManageAlerts && health && (
        <div className={cn(
          "flex items-center flex-wrap gap-x-4 gap-y-1 text-[11px] px-3 py-2 rounded-lg border",
          health.stalled
            ? "bg-red-50 border-red-200 text-red-700 dark:bg-red-950/20 dark:border-red-900 dark:text-red-400"
            : "bg-muted/40 text-muted-foreground"
        )}>
          <span className="inline-flex items-center gap-1 font-medium">
            <span className={cn("w-1.5 h-1.5 rounded-full", health.running && !health.stalled ? "bg-emerald-500" : "bg-red-500")} />
            Engine {health.stalled ? "STALLED" : health.running ? "running" : "stopped"}
          </span>
          <span>last run {health.last_run_at ? timeAgo(health.last_run_at) : "—"}{health.last_run_ms != null ? ` (${health.last_run_ms}ms)` : ""}</span>
          <span>{health.enabled_rule_count} enabled rule{health.enabled_rule_count === 1 ? "" : "s"}</span>
          <span>next run {health.next_run_at ? timeUntil(health.next_run_at) : "—"}</span>
          {health.stalled && <span className="font-medium">— evaluation loop may be stalled</span>}
        </div>
      )}

      {/* Rules Table */}
      <div className="bg-card border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-muted-foreground">
                    <th className="text-left py-3 px-3 text-xs font-medium">Name</th>
                    <th className="text-left py-3 px-3 text-xs font-medium">Site</th>
                    <th className="text-left py-3 px-3 text-xs font-medium">Severity</th>
                    <th className="text-left py-3 px-3 text-xs font-medium">Source</th>
                    <th className="text-left py-3 px-3 text-xs font-medium">Metric</th>
                    <th className="text-center py-3 px-3 text-xs font-medium">Condition</th>
                    <th className="text-center py-3 px-3 text-xs font-medium">State</th>
                    <th className="text-center py-3 px-3 text-xs font-medium">Enabled</th>
                    <th className="text-right py-3 px-3 text-xs font-medium">Actions</th>
                  </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b animate-pulse">
                    {Array.from({ length: 9 }).map((_, j) => (
                      <td key={j} className="py-3 px-3"><div className="h-4 bg-muted rounded" /></td>
                    ))}
                  </tr>
                ))
              ) : rules.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-12 text-center text-muted-foreground">
                    No alert rules configured. Create your first rule to get started.
                  </td>
                </tr>
              ) : (
                rules.map((rule) => (
                  <tr key={rule.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                    <td className="py-2.5 px-3 font-medium">{rule.name}</td>
                    <td className="py-2.5 px-3 text-xs text-muted-foreground">{rule.site_name || "\u2014"}</td>
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
                      {(() => {
                        const s = STATE_STYLE[rule.state || "INACTIVE"] || STATE_STYLE.INACTIVE;
                        const showAge = (rule.state === "FIRING" || rule.state === "PENDING") && rule.last_state_change_at;
                        return (
                          <div className="inline-flex flex-col items-center gap-0.5">
                            <span className={cn("inline-flex items-center gap-1 text-[11px] font-medium", s.text)}>
                              <span className={cn("w-1.5 h-1.5 rounded-full", s.dot)} />
                              {s.label}{showAge ? ` ${timeAgo(rule.last_state_change_at).replace(" ago", "")}` : ""}
                            </span>
                            {rule.last_read_degraded ? (
                              <span
                                className="text-[9px] text-amber-600 dark:text-amber-400"
                                title="Last OpenSearch read was degraded — state held, not evaluated"
                              >
                                ⚠ data delayed
                              </span>
                            ) : rule.last_evaluated_at ? (
                              <span className="text-[9px] text-muted-foreground" title={new Date(rule.last_evaluated_at).toLocaleString()}>
                                {timeAgo(rule.last_evaluated_at)}
                              </span>
                            ) : (
                              <span className="text-[9px] text-muted-foreground">not yet evaluated</span>
                            )}
                          </div>
                        );
                      })()}
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

              {/* Name + Site */}
              <div className="grid grid-cols-2 gap-3">
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
                <div>
                  <label className="text-xs font-medium">Site Name</label>
                  {/* Canonical sites only — free text silently fell back to DC in the engine
                      (site_name or "Site_FGT-DC"). Reset target_key: the device/interface
                      picker is keyed on the site. */}
                  <select
                    value={SITES.includes(form.site_name) ? form.site_name : SITES[0]}
                    onChange={(e) => setForm({ ...form, site_name: e.target.value, target_key: "" })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    {SITES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
              </div>

              {/* Severity + Kind + Data Source */}
              <div className="grid grid-cols-3 gap-3">
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
                  <label className="text-xs font-medium">Kind</label>
                  <select
                    value={form.kind}
                    onChange={(e) => setForm({ ...form, kind: e.target.value as "single" | "composite" })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    <option value="single">Single</option>
                    <option value="composite">Composite</option>
                  </select>
                </div>
                {!isComposite ? (
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
                ) : (
                  <div>
                    <label className="text-xs font-medium">Notify when</label>
                    <div className="flex mt-1 rounded-md border overflow-hidden">
                      {([["all", "All (AND)"], ["any", "Any (OR)"]] as const).map(([val, lbl]) => (
                        <button
                          key={val}
                          type="button"
                          onClick={() => setForm({ ...form, notify_when: val })}
                          className={"flex-1 px-2 py-1.5 text-xs " + (form.notify_when === val ? "bg-blue-600 text-white" : "bg-background hover:bg-muted")}
                        >
                          {lbl}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {!isComposite && (<>
              {/* Metric — catalog-driven (choose, don't type) */}
              <div>
                <label className="text-xs font-medium">Metric</label>
                {fields.length === 0 ? (
                  <div className="w-full px-3 py-1.5 text-sm rounded-md border bg-muted/50 mt-1 text-muted-foreground">
                    Loading metrics…
                  </div>
                ) : (
                  <select
                    value={form.metric_field}
                    onChange={(e) => {
                      const f = fields.find((x) => x.field_key === e.target.value);
                      if (f) applyField(f);
                    }}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    {["traffic", "state"].filter((cat) => fields.some((f) => f.category === cat)).map((cat) => (
                      <optgroup key={cat} label={cat === "traffic" ? "Traffic" : "State"}>
                        {fields.filter((f) => f.category === cat).map((f) => (
                          <option key={f.field_key} value={f.field_key}>
                            {f.display_name}{f.unit ? ` (${f.unit})` : ""}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                    {/* any uncategorized fields */}
                    {fields.filter((f) => f.category !== "traffic" && f.category !== "state").map((f) => (
                      <option key={f.field_key} value={f.field_key}>
                        {f.display_name}{f.unit ? ` (${f.unit})` : ""}
                      </option>
                    ))}
                  </select>
                )}
                {selectedField?.description && (
                  <p className="text-[10px] text-muted-foreground mt-1">{selectedField.description}</p>
                )}
              </div>

              {/* SD-WAN link picker (sdwan_sla only) → target_key = link number */}
              {isSdwan && (
                <div>
                  <label className="text-xs font-medium">Link</label>
                  {sdwanLinks.length === 0 ? (
                    <div className="w-full px-3 py-1.5 text-sm rounded-md border bg-muted/50 mt-1 text-muted-foreground">
                      Loading links…
                    </div>
                  ) : (
                    <select
                      value={form.target_key}
                      onChange={(e) => setForm({ ...form, target_key: e.target.value })}
                      className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                    >
                      {sdwanLinks.map((l) => (
                        <option key={l.key} value={l.key}>{l.label}</option>
                      ))}
                    </select>
                  )}
                </div>
              )}

              {/* Interface picker (interface_stats only) → target_key */}
              {isIface && (
                <div>
                  <label className="text-xs font-medium">Interface</label>
                  {interfaces.length === 0 ? (
                    <div className="w-full px-3 py-1.5 text-sm rounded-md border bg-muted/50 mt-1 text-muted-foreground">
                      Loading interfaces…
                    </div>
                  ) : (
                    <select
                      value={form.target_key}
                      onChange={(e) => setForm({ ...form, target_key: e.target.value })}
                      className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                    >
                      {interfaces.map((i) => (
                        <option key={i.key} value={i.key}>{i.label} (ifIndex {i.key})</option>
                      ))}
                    </select>
                  )}
                </div>
              )}

              {/* Device picker (device_uptime only) → target_key. Optional: blank = any device. */}
              {isDevice && !isSiteLevelMetric && (
                <div>
                  <label className="text-xs font-medium">Device</label>
                  <select
                    value={form.target_key}
                    onChange={(e) => setForm({ ...form, target_key: e.target.value })}
                    className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                  >
                    <option value="">Any device at the site</option>
                    {devices.map((d) => (
                      <option key={d.key} value={d.key}>{d.label} ({d.key})</option>
                    ))}
                  </select>
                </div>
              )}
              {isSiteLevelMetric && (
                <p className="text-xs text-muted-foreground">
                  Collector-gap is a site-level signal — it fires once when the whole site goes silent, so no device is selected.
                </p>
              )}

              {/* Threshold controls — specialized for SD-WAN link state & interface throughput */}
              {isSdwanStatus ? (
                <div>
                  <label className="text-xs font-medium">Alert when link is</label>
                  <div className="flex mt-1 rounded-md border overflow-hidden w-fit">
                    {([["Down", ">=", 1], ["Up", "==", 0]] as const).map(([lbl, cond, thr]) => {
                      const active = lbl === "Up" ? sdwanWantsUp : !sdwanWantsUp;
                      return (
                        <button
                          key={lbl}
                          type="button"
                          onClick={() => setForm({ ...form, aggregation: "max", condition: cond, threshold_value: thr })}
                          className={
                            "px-4 py-1.5 text-sm " +
                            (active
                              ? (lbl === "Down" ? "bg-red-600 text-white" : "bg-emerald-600 text-white")
                              : "bg-background hover:bg-muted")
                          }
                        >
                          {lbl === "Down" ? "🔴 Down" : "🟢 Up"}
                        </button>
                      );
                    })}
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-1">
                    Fires when the link is {sdwanWantsUp ? "Up (status = 0)" : "Down (status ≥ 1)"} — debounced by the sustain window.
                  </p>
                </div>
              ) : isThroughput ? (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs font-medium">Aggregation</label>
                      <select
                        value={form.aggregation}
                        onChange={(e) => setForm({ ...form, aggregation: e.target.value })}
                        className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                      >
                        {metricAggs.map((a) => <option key={a} value={a}>{a}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs font-medium">Threshold mode</label>
                      <div className="flex mt-1 rounded-md border overflow-hidden">
                        <button
                          type="button"
                          onClick={() => setForm({ ...form, link_max_mbps: null, condition: ">" })}
                          className={"flex-1 px-2 py-1.5 text-xs " + (!thrIsPct ? "bg-blue-600 text-white" : "bg-background hover:bg-muted")}
                        >
                          Absolute Mbps
                        </button>
                        <button
                          type="button"
                          onClick={() => { const max = form.link_max_mbps || 1000; setForm({ ...form, link_max_mbps: max, condition: ">", threshold_value: Math.round(max * 90 / 100) }); }}
                          className={"flex-1 px-2 py-1.5 text-xs " + (thrIsPct ? "bg-blue-600 text-white" : "bg-background hover:bg-muted")}
                        >
                          % of Link Max
                        </button>
                      </div>
                    </div>
                  </div>
                  {!thrIsPct ? (
                    <div>
                      <label className="text-xs font-medium">Threshold (Mbps)</label>
                      <NumberField
                        value={form.threshold_value}
                        onValueChange={(n) => setForm({ ...form, threshold_value: n })}
                        className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                      />
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium">Link Max (Mbps)</label>
                        <NumberField
                          value={form.link_max_mbps ?? 0}
                          onValueChange={(max) => setForm({ ...form, link_max_mbps: max, threshold_value: Math.round(max * thrPct / 100) })}
                          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium">Alert above (%)</label>
                        <NumberField
                          value={thrPct}
                          onValueChange={(pct) => { const max = form.link_max_mbps || 1000; setForm({ ...form, link_max_mbps: max, threshold_value: Math.round(max * pct / 100) }); }}
                          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                        />
                        <p className="text-[10px] text-muted-foreground mt-1">= {form.threshold_value} Mbps peak</p>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="text-xs font-medium">Aggregation</label>
                    <select
                      value={form.aggregation}
                      onChange={(e) => setForm({ ...form, aggregation: e.target.value })}
                      className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                    >
                      {metricAggs.map((a) => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs font-medium">Condition</label>
                    <select
                      value={form.condition}
                      onChange={(e) => setForm({ ...form, condition: e.target.value })}
                      className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                    >
                      {metricConds.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs font-medium">Threshold{selectedField?.unit ? ` (${selectedField.unit})` : ""}</label>
                    <NumberField
                      value={form.threshold_value}
                      onValueChange={(n) => setForm({ ...form, threshold_value: n })}
                      className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                    />
                  </div>
                </div>
              )}
              </>)}

              {/* Composite clause editor — combine multiple metrics with AND/OR */}
              {isComposite && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium">
                      Metrics — fires when <b>{form.notify_when === "all" ? "ALL" : "ANY"}</b> match
                    </label>
                    <button type="button" onClick={addClause} className="text-xs px-2 py-1 rounded-md border hover:bg-muted">
                      + Add metric
                    </button>
                  </div>
                  {form.clauses.length < 2 && (
                    <p className="text-[11px] text-muted-foreground">Add at least two metrics to combine with AND/OR.</p>
                  )}
                  {form.clauses.map((c, i) => {
                    const cf = fieldsForSource(c.data_source);
                    const sel = cf.find((f) => f.field_key === c.metric_field) || null;
                    const aggs = sel?.valid_aggregations?.length ? sel.valid_aggregations : AGGREGATIONS;
                    const conds = sel?.valid_conditions?.length ? sel.valid_conditions : CONDITIONS;
                    return (
                      <div key={i} className="rounded-lg border p-2 space-y-2 bg-muted/30">
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] font-semibold text-muted-foreground">Metric {i + 1}</span>
                          <button type="button" onClick={() => removeClause(i)} className="text-[11px] text-red-600 hover:underline">Remove</button>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                          <select value={c.data_source} onChange={(e) => setClauseSource(i, e.target.value)} className="px-2 py-1 text-xs rounded-md border bg-background">
                            {DATA_SOURCES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                          </select>
                          <select value={c.metric_field} onChange={(e) => setClauseMetric(i, e.target.value)} className="px-2 py-1 text-xs rounded-md border bg-background">
                            {cf.length === 0 && <option value="">Loading…</option>}
                            {cf.map((f) => <option key={f.field_key} value={f.field_key}>{f.display_name}{f.unit ? ` (${f.unit})` : ""}</option>)}
                          </select>
                        </div>
                        {c.data_source === "interface_stats" && (
                          <select value={c.target_key} onChange={(e) => patchClause(i, { target_key: e.target.value })} className="w-full px-2 py-1 text-xs rounded-md border bg-background">
                            <option value="">Select interface…</option>
                            {interfaces.map((it) => <option key={it.key} value={it.key}>{it.label} (ifIndex {it.key})</option>)}
                          </select>
                        )}
                        {c.data_source === "sdwan_sla" && (
                          <select value={c.target_key} onChange={(e) => patchClause(i, { target_key: e.target.value })} className="w-full px-2 py-1 text-xs rounded-md border bg-background">
                            <option value="">Select link…</option>
                            {sdwanLinks.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
                          </select>
                        )}
                        {c.data_source === "device_uptime" && c.metric_field !== "collector_gap" && (
                          <select value={c.target_key} onChange={(e) => patchClause(i, { target_key: e.target.value })} className="w-full px-2 py-1 text-xs rounded-md border bg-background">
                            <option value="">Any device at the site</option>
                            {devices.map((d) => <option key={d.key} value={d.key}>{d.label} ({d.key})</option>)}
                          </select>
                        )}
                        <div className="grid grid-cols-3 gap-2">
                          <select value={c.aggregation} onChange={(e) => patchClause(i, { aggregation: e.target.value })} className="px-2 py-1 text-xs rounded-md border bg-background">
                            {aggs.map((a) => <option key={a} value={a}>{a}</option>)}
                          </select>
                          <select value={c.condition} onChange={(e) => patchClause(i, { condition: e.target.value })} className="px-2 py-1 text-xs rounded-md border bg-background">
                            {conds.map((cc) => <option key={cc} value={cc}>{cc}</option>)}
                          </select>
                          <NumberField value={c.threshold_value} onValueChange={(n) => patchClause(i, { threshold_value: n })} className="px-2 py-1 text-xs rounded-md border bg-background" />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Window + Sustained */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium">Eval Window (min)</label>
                  <NumberField
                    min={isIface ? IFACE_MIN_WINDOW : isDevice ? DEVICE_MIN_WINDOW : 1}
                    value={form.evaluation_window_minutes}
                    onValueChange={(n) => setForm({ ...form, evaluation_window_minutes: n })}
                    className={cn(
                      "w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1",
                      (windowInvalid || ifaceWindowTooShort || deviceWindowTooShort) && "border-red-400"
                    )}
                  />
                  {windowInvalid && !ifaceWindowTooShort && !deviceWindowTooShort && (
                    <p className="text-[10px] text-red-600 dark:text-red-400 mt-1">
                      Eval window must be at least 1 minute.
                    </p>
                  )}
                  {ifaceWindowTooShort && (
                    <p className="text-[10px] text-red-600 dark:text-red-400 mt-1">
                      Interface bandwidth needs ≥ {IFACE_MIN_WINDOW} min (rate derivative).
                    </p>
                  )}
                  {deviceWindowTooShort && (
                    <p className="text-[10px] text-red-600 dark:text-red-400 mt-1">
                      Device availability needs ≥ {DEVICE_MIN_WINDOW} min (else a dropped poll false-fires).
                    </p>
                  )}
                </div>
                <div>
                  <label className="text-xs font-medium">Sustained For (min)</label>
                  <NumberField
                    min={0}
                    value={form.sustained_for_minutes}
                    onValueChange={(n) => setForm({ ...form, sustained_for_minutes: n })}
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

              {/* Message template assignment (§11.1) */}
              <div>
                <label className="text-xs font-medium">Message template</label>
                <select
                  value={form.notification_template_id}
                  onChange={(e) => setForm({ ...form, notification_template_id: e.target.value })}
                  className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                >
                  <option value="">
                    Use default{(() => { const d = messageTemplates.find((t) => t.is_default); return d ? ` (${d.name})` : ""; })()}
                  </option>
                  {messageTemplates.filter((t) => t.is_active).map((t) => (
                    <option key={t.id} value={t.id}>{t.name}{t.is_default ? " — default" : ""}</option>
                  ))}
                </select>
                <p className="text-[10px] text-muted-foreground mt-1">
                  Different rules can use different templates. Leave as “Use default” to follow the default. Manage in Settings → Message Templates.
                </p>
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
                  disabled={saving || !form.name || windowInvalid || ifaceWindowTooShort || deviceWindowTooShort || (isIface && !form.target_key) || compositeInvalid}
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
