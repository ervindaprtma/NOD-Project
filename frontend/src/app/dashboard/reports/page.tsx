"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { getAccessToken, apiFetch, hasMinRole, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Types ────────────────────────────────────────────────────

interface ReportJob {
  job_id: string;
  report_type: string;
  output_format: string;
  status: "pending" | "running" | "completed" | "failed";
  file_size_bytes: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
}

interface ReportListResponse {
  success: boolean;
  data: ReportJob[];
}

interface GenerateResponse {
  success: boolean;
  data: { job_id: string; status: string };
}

interface StatusResponse {
  success: boolean;
  data: ReportJob;
}

// ── Constants ─────────────────────────────────────────────────

const REPORT_TYPES = [
  { id: "R-01", title: "Traffic Internet", desc: "Top apps, throughput, AS, countries, protocol, per-site" },
  { id: "R-02", title: "Resource Usage", desc: "Per-device CPU, memory, sessions, HA status" },
  { id: "R-03", title: "VPN Users", desc: "SSL VPN & IPsec VPN active user counts" },
  { id: "R-04", title: "SD-WAN SLA", desc: "Latency, jitter, packet loss, link status per site" },
  { id: "R-05", title: "Traffic Inbound", desc: "Top services, client AS, countries, egress interfaces" },
  { id: "R-06", title: "Traffic Internal", desc: "Intra-LAN, inter-site flow, top services" },
  { id: "R-07", title: "Executive Summary", desc: "KPI dashboard, 1-page overview" },
  { id: "R-08", title: "All-in-One", desc: "Combined report: all sections" },
];

const SITES = [
  { id: "Site_FGT-DC", label: "DC" },
  { id: "Site_FGT-DRC", label: "DRC" },
  { id: "Site_FGT_Office", label: "Office" },
];

const SECTIONS: Record<string, { id: string; label: string }[]> = {
  "R-01": [
    { id: "top_apps", label: "Top Applications" },
    { id: "throughput", label: "Throughput Timeline" },
    { id: "top_as", label: "Top AS Orgs" },
    { id: "top_countries", label: "Top Countries" },
    { id: "protocol_dist", label: "Protocol Distribution" },
    { id: "per_site", label: "Per-Site Breakdown" },
  ],
  "R-02": [
    { id: "device_status", label: "Device Status" },
    { id: "cpu_timeline", label: "CPU Timeline" },
    { id: "memory_timeline", label: "Memory Timeline" },
    { id: "session_timeline", label: "Session Timeline" },
  ],
  "R-03": [
    { id: "ssl_vpn", label: "SSL VPN" },
    { id: "ipsec_vpn", label: "IPsec VPN" },
  ],
  "R-04": [
    { id: "latency", label: "Latency" },
    { id: "jitter", label: "Jitter" },
    { id: "packet_loss", label: "Packet Loss" },
    { id: "link_status", label: "Link Status" },
  ],
  "R-05": [
    { id: "top_services", label: "Top Services" },
    { id: "top_client_as", label: "Top Client AS" },
    { id: "top_countries", label: "Top Countries" },
    { id: "egress", label: "Egress Interfaces" },
  ],
  "R-06": [
    { id: "top_services", label: "Top Services" },
    { id: "top_clients", label: "Top Client IPs" },
    { id: "top_servers", label: "Top Server IPs" },
  ],
};

const FORMATS = [
  { id: "pdf", label: "PDF", ext: ".pdf" },
  { id: "html", label: "HTML", ext: ".html" },
  { id: "docx", label: "DOCX", ext: ".docx" },
];

const TIME_PRESETS = [
  { label: "15m", seconds: 900 },
  { label: "1h", seconds: 3600 },
  { label: "2h", seconds: 7200 },
  { label: "4h", seconds: 14400 },
  { label: "12h", seconds: 43200 },
  { label: "24h", seconds: 86400 },
];

const CHANNELS = [
  { id: "email", label: "Email", icon: "📧" },
  { id: "telegram", label: "Telegram", icon: "📱" },
  { id: "discord", label: "Discord", icon: "💬" },
  { id: "whatsapp", label: "WhatsApp", icon: "💬" },
];

// ── Helpers ───────────────────────────────────────────────────

function formatBytes(b: number | null): string {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 ** 2).toFixed(1)} MB`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function statusBadge(s: string) {
  const map: Record<string, string> = {
    pending: "bg-muted text-muted-foreground",
    running: "bg-blue-500/10 text-blue-600",
    completed: "bg-emerald-500/10 text-emerald-600",
    failed: "bg-red-500/10 text-red-600",
  };
  return (
    <span className={cn("px-2 py-0.5 rounded text-xs font-medium", map[s] || "bg-muted text-muted-foreground")}>
      {s}
    </span>
  );
}

// ── Main Page ─────────────────────────────────────────────────

export default function ReportsPage() {
  // Form state
  const [reportType, setReportType] = useState("R-01");
  const [outputFormat, setOutputFormat] = useState("pdf");
  const [timePreset, setTimePreset] = useState("15m");
  const [customGte, setCustomGte] = useState("");
  const [customLte, setCustomLte] = useState("");
  const [useCustom, setUseCustom] = useState(false);
  const canGenerateReports = hasMinRole("operator");
  const [generating, setGenerating] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [showWarning, setShowWarning] = useState(false);

  // Site & section selection
  const [selectedSites, setSelectedSites] = useState<string[]>(["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]);
  const [selectedSections, setSelectedSections] = useState<string[]>([]);

  // Distribute state
  const [distChannels, setDistChannels] = useState<string[]>([]);
  const [distEmail, setDistEmail] = useState("");
  const [distPhone, setDistPhone] = useState("");
  const [distJobId, setDistJobId] = useState<string | null>(null);
  const token = typeof window !== "undefined" ? getAccessToken() : null;

  // Report list (poll every 5s)
  const { data: listData, mutate: refreshList } = useSWR<ReportListResponse>(
    token ? "/api/v1/reports" : null,
    apiFetch,
    { refreshInterval: 5000 }
  );

  // Active job status (poll every 2s while generating)
  const jobId = activeJobId || (listData?.data?.find((j) => j.status === "running")?.job_id ?? null);
  const { data: statusData } = useSWR<StatusResponse>(
    jobId ? `/api/v1/reports/status/${jobId}` : null,
    apiFetch,
    { refreshInterval: 2000 }
  );

  // Auto-clear active job when done
  useEffect(() => {
    if (statusData?.data?.status === "completed" || statusData?.data?.status === "failed") {
      const t = setTimeout(() => {
        setActiveJobId(null);
        refreshList();
      }, 2000);
      return () => clearTimeout(t);
    }
  }, [statusData?.data?.status, refreshList]);

  // ── Time range ──────────────────────────────────────────

  function getTimeRange(): { gte: number; lte: number } {
    if (useCustom && customGte && customLte) {
      return { gte: new Date(customGte).getTime(), lte: new Date(customLte).getTime() };
    }
    const now = Date.now();
    const preset = TIME_PRESETS.find((p) => p.label === timePreset) || TIME_PRESETS[0];
    return { gte: now - preset.seconds * 1000, lte: now };
  }

  // ── Generate ────────────────────────────────────────────

  async function handleGenerate() {
    const { gte, lte } = getTimeRange();
    const durationSec = (lte - gte) / 1000;

    // FR-14: 24h warning
    if (durationSec > 86400) {
      setShowWarning(true);
      return;
    }

    await doGenerate(gte, lte);
  }

  async function doGenerate(gte: number, lte: number) {
    setGenerating(true);
    setShowWarning(false);
    try {
      const res = await apiFetch<GenerateResponse>(
        "/api/v1/reports/generate",
        {
          method: "POST",
          body: JSON.stringify({
          report_type: reportType,
          output_format: outputFormat,
          time_range_start: gte,
          time_range_end: lte,
          sites: selectedSites,
          sections: selectedSections.length > 0 ? selectedSections : undefined,
        }),
        },
      );
      if (res.success) {
        setActiveJobId(res.data.job_id);
        refreshList();
      }
    } catch (e) {
      console.error("Generate failed", e);
    } finally {
      setGenerating(false);
    }
  }

  // ── Download ────────────────────────────────────────────

  async function handleDownload(job: ReportJob) {
    try {
      const token = getAccessToken();
      const res = await fetch(`/api/v1/reports/download/${job.job_id}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        credentials: "include",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `nod_report_${job.job_id.slice(0, 8)}.${job.output_format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("Download failed", e);
    }
  }

  // ── Distribute ──────────────────────────────────────────

  async function handleDistribute(job: ReportJob) {
    if (distChannels.length === 0) return;
    try {
      await apiFetch(`/api/v1/reports/distribute/${job.job_id}`, {
        method: "POST",
        body: JSON.stringify({
          channels: distChannels,
          recipient_email: distEmail || undefined,
          recipient_phone: distPhone || undefined,
        }),
      });
      setDistJobId(null);
      setDistChannels([]);
    } catch (e) {
      console.error("Distribute failed", e);
    }
  }

  // ── Render ──────────────────────────────────────────────

  const jobs = listData?.data || [];
  const activeStatus = statusData?.data;

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Reports</h1>
        <p className="text-sm text-muted-foreground">FR-12 Report Export Engine · FR-13 Distribution</p>
      </div>

      {/* ── Generate Form ─────────────────────────────── */}
      <div className="bg-card border rounded-lg p-6 space-y-4">
        <h2 className="text-lg font-semibold">Generate Report</h2>

        {/* Report Type */}
        <div>
          <label className="text-sm font-medium mb-2 block">Report Type</label>
          <div className="grid grid-cols-4 gap-2">
            {REPORT_TYPES.map((rt) => (
              <button
                key={rt.id}
                onClick={() => setReportType(rt.id)}
                className={cn(
                  "text-left p-3 rounded-md border transition-colors",
                  reportType === rt.id
                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                    : "border-border hover:bg-muted/50"
                )}
              >
                <span className="text-sm font-semibold">{rt.id} — {rt.title}</span>
                <p className="text-xs text-muted-foreground mt-0.5">{rt.desc}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Sites */}
        <div>
          <label className="text-sm font-medium mb-2 block">Sites</label>
          <div className="flex gap-2">
            {SITES.map((site) => (
              <label
                key={site.id}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-2 rounded-md border text-sm font-medium cursor-pointer transition-colors",
                  selectedSites.includes(site.id)
                    ? "border-primary bg-primary/5 text-foreground"
                    : "border-border text-muted-foreground hover:bg-muted"
                )}
              >
                <input
                  type="checkbox"
                  checked={selectedSites.includes(site.id)}
                  onChange={() =>
                    setSelectedSites((prev) =>
                      prev.includes(site.id) ? prev.filter((s) => s !== site.id) : [...prev, site.id]
                    )
                  }
                  className="h-3.5 w-3.5 rounded border-border"
                />
                {site.label}
              </label>
            ))}
          </div>
        </div>

        {/* Sections (optional) */}
        {SECTIONS[reportType] && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-medium">Sections <span className="text-muted-foreground font-normal">(optional — leave all checked for full report)</span></label>
              <button
                onClick={() =>
                  setSelectedSections((prev) =>
                    prev.length === SECTIONS[reportType].length ? [] : SECTIONS[reportType].map((s) => s.id)
                  )
                }
                className="text-xs text-primary hover:underline"
              >
                {selectedSections.length === SECTIONS[reportType].length ? "Deselect All" : "Select All"}
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {SECTIONS[reportType].map((sec) => (
                <label
                  key={sec.id}
                  className={cn(
                    "flex items-center gap-1.5 px-2 py-1.5 rounded-md border text-xs cursor-pointer transition-colors",
                    selectedSections.includes(sec.id)
                      ? "border-primary bg-primary/5 text-foreground"
                      : "border-border text-muted-foreground hover:bg-muted"
                  )}
                >
                  <input
                    type="checkbox"
                    checked={selectedSections.includes(sec.id)}
                    onChange={() =>
                      setSelectedSections((prev) =>
                        prev.includes(sec.id) ? prev.filter((s) => s !== sec.id) : [...prev, sec.id]
                      )
                    }
                    className="h-3 w-3 rounded border-border"
                  />
                  {sec.label}
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Format */}
        <div>
          <label className="text-sm font-medium mb-2 block">Output Format</label>
          <div className="flex gap-2">
            {FORMATS.map((f) => (
              <button
                key={f.id}
                onClick={() => setOutputFormat(f.id)}
                className={cn(
                  "px-4 py-2 rounded-md border text-sm font-medium transition-colors",
                  outputFormat === f.id
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border hover:bg-muted"
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* Time Range */}
        <div>
          <label className="text-sm font-medium mb-2 block">Time Range</label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => setUseCustom(false)}
              className={cn(
                "px-3 py-1.5 text-xs rounded-md border transition-colors",
                !useCustom ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"
              )}
            >
              Preset
            </button>
            <button
              onClick={() => setUseCustom(true)}
              className={cn(
                "px-3 py-1.5 text-xs rounded-md border transition-colors",
                useCustom ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"
              )}
            >
              Custom
            </button>
            {!useCustom ? (
              <div className="flex gap-1 bg-muted rounded-md p-1">
                {TIME_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    onClick={() => setTimePreset(p.label)}
                    className={cn(
                      "px-2.5 py-1 text-xs rounded-sm transition-colors",
                      timePreset === p.label
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <input
                  type="datetime-local"
                  value={customGte}
                  onChange={(e) => setCustomGte(e.target.value)}
                  className="px-2 py-1.5 text-xs rounded-md border bg-background"
                />
                <span className="text-xs text-muted-foreground">to</span>
                <input
                  type="datetime-local"
                  value={customLte}
                  onChange={(e) => setCustomLte(e.target.value)}
                  className="px-2 py-1.5 text-xs rounded-md border bg-background"
                />
              </div>
            )}
          </div>
        </div>

        {/* Generate Button */}
        <button
          onClick={handleGenerate}
          disabled={generating || !canGenerateReports}
          className={cn(
            "px-6 py-2.5 rounded-md text-sm font-medium transition-colors",
            generating || !canGenerateReports
              ? "bg-muted text-muted-foreground cursor-not-allowed"
              : "bg-primary text-primary-foreground hover:bg-primary/90"
          )}
        >
          {!canGenerateReports ? "Requires operator+" : generating ? "Generating..." : "Generate Report"}
        </button>

        {/* Active Job Status */}
        {activeStatus && activeStatus.status !== "completed" && (
          <div className="flex items-center gap-2 text-sm">
            <span className="animate-spin">⏳</span>
            <span className="text-muted-foreground">
              Job <code className="text-xs bg-muted px-1 rounded">{activeStatus.job_id.slice(0, 8)}</code> —{" "}
              {statusBadge(activeStatus.status)}
            </span>
          </div>
        )}
      </div>

      {/* ── Report History ─────────────────────────────── */}
      <div className="bg-card border rounded-lg p-6 space-y-4">
        <h2 className="text-lg font-semibold">Report History</h2>

        {jobs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No reports generated yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left">
                  <th className="py-2 pr-3 font-medium">Job</th>
                  <th className="py-2 pr-3 font-medium">Type</th>
                  <th className="py-2 pr-3 font-medium">Format</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Size</th>
                  <th className="py-2 pr-3 font-medium">Created</th>
                  <th className="py-2 pr-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.job_id} className="border-b last:border-0 hover:bg-muted/30">
                    <td className="py-2 pr-3">
                      <code className="text-xs bg-muted px-1 rounded">{job.job_id.slice(0, 8)}</code>
                    </td>
                    <td className="py-2 pr-3 text-xs">{job.report_type}</td>
                    <td className="py-2 pr-3 text-xs uppercase">{job.output_format}</td>
                    <td className="py-2 pr-3">{statusBadge(job.status)}</td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">{formatBytes(job.file_size_bytes)}</td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">{formatDate(job.created_at)}</td>
                    <td className="py-2 pr-3">
                      <div className="flex gap-1">
                        {job.status === "completed" && canGenerateReports && (
                          <>
                            <button
                              onClick={() => handleDownload(job)}
                              className="px-2 py-1 text-xs rounded bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/20"
                            >
                              Download
                            </button>
                            {job.output_format === "html" && (
                              <button
                                onClick={async () => {
                                  try {
                                    const tk = getAccessToken();
                                    const res = await fetch(`/api/v1/reports/preview/${job.job_id}`, {
                                      headers: tk ? { Authorization: `Bearer ${tk}` } : {},
                                      credentials: "include",
                                    });
                                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                                    const blob = await res.blob();
                                    const blobUrl = URL.createObjectURL(blob);
                                    window.open(blobUrl, "_blank");
                                  } catch (e) {
                                    console.error("Preview failed", e);
                                  }
                                }}
                                className="px-2 py-1 text-xs rounded bg-blue-500/10 text-blue-600 hover:bg-blue-500/20"
                              >
                                Preview
                              </button>
                            )}
                            <button
                              onClick={() => setDistJobId(distJobId === job.job_id ? null : job.job_id)}
                              className="px-2 py-1 text-xs rounded bg-blue-500/10 text-blue-600 hover:bg-blue-500/20"
                            >
                              Distribute
                            </button>
                          </>
                        )}
                        {job.status === "completed" && !canGenerateReports && (
                          <span className="text-[11px] text-muted-foreground">—</span>
                        )}
                        {job.status === "failed" && job.error_message && (
                          <span className="text-xs text-red-500 truncate max-w-[120px]" title={job.error_message}>
                            {job.error_message.slice(0, 40)}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Distribute Panel ────────────────────────────── */}
      {distJobId && (
        <div className="bg-card border rounded-lg p-6 space-y-4">
          <h2 className="text-lg font-semibold">
            Distribute Report <code className="text-xs bg-muted px-1 rounded">{distJobId.slice(0, 8)}</code>
          </h2>

          <div>
            <label className="text-sm font-medium mb-2 block">Channels</label>
            <div className="flex flex-wrap gap-2">
              {CHANNELS.map((ch) => (
                <button
                  key={ch.id}
                  onClick={() =>
                    setDistChannels((prev) =>
                      prev.includes(ch.id) ? prev.filter((c) => c !== ch.id) : [...prev, ch.id]
                    )
                  }
                  className={cn(
                    "px-3 py-1.5 rounded-md border text-sm transition-colors",
                    distChannels.includes(ch.id)
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border hover:bg-muted"
                  )}
                >
                  {ch.icon} {ch.label}
                </button>
              ))}
            </div>
          </div>

          {distChannels.includes("email") && (
            <div>
              <label className="text-sm font-medium mb-1 block">Recipient Email</label>
              <input
                type="email"
                value={distEmail}
                onChange={(e) => setDistEmail(e.target.value)}
                placeholder="user@example.com"
                className="px-3 py-2 rounded-md border bg-background text-sm w-full max-w-sm"
              />
            </div>
          )}

          {distChannels.includes("whatsapp") && (
            <div>
              <label className="text-sm font-medium mb-1 block">Recipient Phone</label>
              <input
                type="tel"
                value={distPhone}
                onChange={(e) => setDistPhone(e.target.value)}
                placeholder="+6281234567890"
                className="px-3 py-2 rounded-md border bg-background text-sm w-full max-w-sm"
              />
            </div>
          )}

          <button
            onClick={() => {
              const job = jobs.find((j) => j.job_id === distJobId);
              if (job) handleDistribute(job);
            }}
            disabled={distChannels.length === 0}
            className={cn(
              "px-6 py-2.5 rounded-md text-sm font-medium transition-colors",
              distChannels.length === 0
                ? "bg-muted text-muted-foreground cursor-not-allowed"
                : "bg-primary text-primary-foreground hover:bg-primary/90"
            )}
          >
            Send Report
          </button>
        </div>
      )}
      {/* ── 24h Warning Dialog ──────────────────────────── */}
      {showWarning && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-card border rounded-lg p-6 max-w-md mx-4 space-y-4 shadow-xl">
            <h3 className="text-lg font-semibold text-destructive">⚠️ Warning</h3>
            <p className="text-sm text-muted-foreground">
              Querying data beyond 24 hours may cause high resource usage on OpenSearch.
              Do you want to continue?
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowWarning(false)}
                className="px-4 py-2 rounded-md border text-sm hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  const { gte, lte } = getTimeRange();
                  doGenerate(gte, lte);
                }}
                className="px-4 py-2 rounded-md bg-destructive text-destructive-foreground text-sm hover:bg-destructive/90"
              >
                Continue Anyway
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
