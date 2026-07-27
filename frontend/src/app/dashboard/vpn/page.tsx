"use client";

import { useState, useRef, useEffect } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { TIME_PRESETS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS, formatBytes, getDefaultTimeRange } from "@/lib/constants";
import TimeRangePicker, { type CustomTimeRange } from "@/components/panels/TimeRangePicker";

interface SSLVPNUser {
  username: string; remote_ip: string; vpn_ip: string;
  bytes_in: number; bytes_out: number; device: string;
}

interface IPsecUser {
  username: string; remote_gw_ip: string; assigned_ip: string;
  bytes_in: number; bytes_out: number; tunnel_lifetime_sec: number; device: string;
}

interface VPNSessionHistoryItem {
  username: string;
  protocol: string;
  site: string;
  session_started: number;
  last_seen: number;
  bytes_in: number;
  bytes_out: number;
  status: string;
}

type SectionId = "ssl" | "ipsec";

function fmtTs(ts: number): string {
  const d = new Date(ts);
  const now = Date.now();
  // within 24h → time only; older → date + time
  if (now - ts < 86_400_000) {
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function VPNPage() {
  const defaultRange = getDefaultTimeRange();
  const [gteMs, setGteMs] = useState(defaultRange.gte_ms);
  const [lteMs, setLteMs] = useState(defaultRange.lte_ms);
  const [selectedPreset, setSelectedPreset] = useState("15m");
  const [activePresetSeconds, setActivePresetSeconds] = useState(TIME_PRESETS[0].seconds);
  const [refreshInterval, setRefreshInterval] = useState(DEFAULT_REFRESH_MS);
  const [expanded, setExpanded] = useState<SectionId | null>(null);
  const [showCustomPicker, setShowCustomPicker] = useState(false);
  const [customRangeLabel, setCustomRangeLabel] = useState<string | null>(null);
  const prevIntervalRef = useRef(DEFAULT_REFRESH_MS);

  const token = typeof window !== "undefined" ? getAccessToken() : null;

  const [currentGteMs, setCurrentGteMs] = useState(defaultRange.gte_ms);
  const [currentLteMs, setCurrentLteMs] = useState(defaultRange.lte_ms);

  useEffect(() => {
    if (activePresetSeconds <= 0) {
      setCurrentGteMs(gteMs);
      setCurrentLteMs(lteMs);
      return;
    }
    const tick = () => {
      const now = Date.now();
      setCurrentGteMs(now - activePresetSeconds * 1000);
      setCurrentLteMs(now);
    };
    tick();
    const id = setInterval(tick, refreshInterval > 0 ? refreshInterval : 60_000);
    return () => clearInterval(id);
  }, [activePresetSeconds, refreshInterval, gteMs, lteMs]);

  const sslKey = token
    ? `/api/v1/vpn/ssl?gte_ms=${currentGteMs}&lte_ms=${currentLteMs}`
    : null;
  const ipsecKey = token
    ? `/api/v1/vpn/ipsec?gte_ms=${currentGteMs}&lte_ms=${currentLteMs}`
    : null;
  // Session history follows the selected range; the server cuts sessions at 00:00
  // WIB and reconstructs true starts from that day's samples.
  const historyKey = token
    ? `/api/v1/vpn/sessions-history?gte_ms=${currentGteMs}&lte_ms=${currentLteMs}`
    : null;

  const { data: sslData, error: sslErr, isLoading: sslLoading } = useSWR<{ data: SSLVPNUser[] }>(
    sslKey, swrFetcher, { refreshInterval: 0 }
  );
  const { data: ipsecData, error: ipsecErr, isLoading: ipsecLoading } = useSWR<{ data: IPsecUser[] }>(
    ipsecKey, swrFetcher, { refreshInterval: 0 }
  );
  const { data: historyData, error: historyErr, isLoading: historyLoading } = useSWR<{ data: VPNSessionHistoryItem[] }>(
    historyKey, swrFetcher, { refreshInterval: 0 }
  );

  const sslUsers = sslData?.data || [];
  const ipsecUsers = ipsecData?.data || [];
  const history = historyData?.data || null;

  function handlePreset(seconds: number, label: string) {
    const now = Date.now();
    setGteMs(now - seconds * 1000);
    setLteMs(now);
    setActivePresetSeconds(seconds);
    setSelectedPreset(label);
    setCustomRangeLabel(null);
    setShowCustomPicker(false);
    // Restore auto-refresh if it was disabled by custom range
    setRefreshInterval(prev => prev === 0 ? prevIntervalRef.current : prev);
  }

  function handleCustomApply(range: CustomTimeRange) {
    setGteMs(range.gte_ms);
    setLteMs(range.lte_ms);
    setActivePresetSeconds(0);
    setSelectedPreset("custom");
    prevIntervalRef.current = refreshInterval > 0 ? refreshInterval : DEFAULT_REFRESH_MS;
    setRefreshInterval(0);
    setShowCustomPicker(false);
    const from = new Date(range.gte_ms);
    const to = new Date(range.lte_ms);
    setCustomRangeLabel(
      `${from.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${from.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })} — ${to.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${to.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}`
    );
  }

  if (expanded) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setExpanded(null)}
            className="px-3 py-1.5 text-xs border rounded-md hover:bg-muted transition-colors"
          >
            ← Back to VPN Sessions
          </button>
          <h1 className="text-xl font-bold tracking-tight">
            {expanded === "ssl" ? `SSL VPN — ${sslUsers.length} Active` : `IPsec VPN — ${ipsecUsers.length} Active`}
          </h1>
        </div>
        <div className="bg-card border rounded-lg overflow-hidden p-6">
          {expanded === "ssl" ? (
            <VPNTable
              type="ssl"
              users={sslUsers}
              loading={sslLoading}
              error={!!sslErr}
              large
            />
          ) : (
            <VPNTable
              type="ipsec"
              users={ipsecUsers}
              loading={ipsecLoading}
              error={!!ipsecErr}
              large
            />
          )}
        </div>
        <TimeRangePicker
          isOpen={showCustomPicker}
          onApply={handleCustomApply}
          onCancel={() => setShowCustomPicker(false)}
          initialGteMs={gteMs}
          initialLteMs={lteMs}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">VPN Sessions</h1>
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex gap-1 bg-muted rounded-md p-1">
            {TIME_PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => handlePreset(p.seconds, p.label)}
                className={cn(
                  "px-2.5 py-1 text-xs rounded-sm transition-colors",
                  selectedPreset === p.label
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {p.label}
              </button>
            ))}
            <button
              onClick={() => setShowCustomPicker(true)}
              className={cn(
                "px-2.5 py-1 text-xs rounded-sm transition-colors",
                selectedPreset === "custom"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title={customRangeLabel || "Select custom date/time range"}
            >
              {selectedPreset === "custom" && customRangeLabel
                ? customRangeLabel.length > 20
                  ? customRangeLabel.slice(0, 18) + "…"
                  : customRangeLabel
                : "Custom"}
            </button>
          </div>
          <select
            value={refreshInterval}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="h-7 px-2 text-xs border rounded-md bg-card text-muted-foreground cursor-pointer"
          >
            {REFRESH_INTERVALS.map((ri) => (
              <option key={ri.value} value={ri.value}>
                {ri.label === "Off" ? "⏸ Off" : `↻ ${ri.label}`}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* SSL VPN Section */}
      <div className="space-y-3 group">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold">SSL VPN</h2>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-bold bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
              {sslUsers.length} active
            </span>
          </div>
          <button
            onClick={() => setExpanded("ssl")}
            className="text-[11px] text-muted-foreground hover:text-primary transition-colors opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded hover:bg-muted"
          >
            View Full ↗
          </button>
        </div>

        {sslErr && (
          <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-xs">
            Failed to load SSL VPN data.
          </div>
        )}

        <div className="bg-card border rounded-lg overflow-hidden">
          <VPNTable type="ssl" users={sslUsers} loading={sslLoading} error={!!sslErr} />
        </div>
      </div>

      {/* IPsec VPN Section */}
      <div className="space-y-3 group">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold">IPsec VPN</h2>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-bold bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400">
              {ipsecUsers.length} active
            </span>
          </div>
          <button
            onClick={() => setExpanded("ipsec")}
            className="text-[11px] text-muted-foreground hover:text-primary transition-colors opacity-0 group-hover:opacity-100 px-2 py-0.5 rounded hover:bg-muted"
          >
            View Full ↗
          </button>
        </div>

        {ipsecErr && (
          <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-xs">
            Failed to load IPsec VPN data.
          </div>
        )}

        <div className="bg-card border rounded-lg overflow-hidden">
          <VPNTable type="ipsec" users={ipsecUsers} loading={ipsecLoading} error={!!ipsecErr} />
        </div>
      </div>

      {/* Session History Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Session History</h2>
          <span className="text-[11px] text-muted-foreground">
            Selected range · split at 00:00 WIB — {history ? `${history.length} session${history.length !== 1 ? "s" : ""}` : ""}
          </span>
        </div>

        {historyErr && (
          <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-xs">
            Failed to load session history.
          </div>
        )}

        <div className="bg-card border rounded-lg overflow-hidden">
          <SessionHistoryTable items={history} loading={historyLoading} error={!!historyErr} />
        </div>
      </div>

      <TimeRangePicker
        isOpen={showCustomPicker}
        onApply={handleCustomApply}
        onCancel={() => setShowCustomPicker(false)}
        initialGteMs={gteMs}
        initialLteMs={lteMs}
      />
    </div>
  );
}

function VPNTable({
  type, users, loading, error, large,
}: {
  type: "ssl" | "ipsec"; users: any[]; loading: boolean; error: boolean; large?: boolean;
}) {
  const size = large ? "text-sm" : "text-xs";
  const cell = large ? "py-3 px-4" : "py-2 px-3";

  if (error) {
    return <p className="text-sm text-destructive text-center py-6">Failed to load data</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className={`w-full ${size}`}>
        <thead>
          <tr className="border-b bg-muted/50 text-muted-foreground">
            <th className={`text-left ${cell}`}>{type === "ssl" ? "Username" : "User / Tunnel"}</th>
            <th className={`text-left ${cell}`}>{type === "ssl" ? "Remote IP" : "Remote GW"}</th>
            <th className={`text-left ${cell}`}>{type === "ssl" ? "VPN IP" : "Assigned IP"}</th>
            <th className={`text-right ${cell}`}>Bytes In</th>
            <th className={`text-right ${cell}`}>Bytes Out</th>
            <th className={`text-left ${cell}`}>Device</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            Array.from({ length: 3 }).map((_, i) => (
              <tr key={i} className="border-b animate-pulse">
                {[1, 2, 3, 4, 5, 6, 7].map((j) => (
                  <td key={j} className={cell}><div className="h-3 bg-muted rounded" /></td>
                ))}
              </tr>
            ))
          ) : users.length === 0 ? (
            <tr>
              <td colSpan={type === "ipsec" ? 6 : 5} className="py-10 text-center text-muted-foreground">
                No active {type === "ssl" ? "SSL" : "IPsec"} VPN sessions
              </td>
            </tr>
          ) : (
            users.map((u, i) => (
              <tr key={i} className="border-b last:border-0 hover:bg-muted/30">
                <td className={`${cell} font-medium font-mono`}>{u.username}</td>
                <td className={`${cell} font-mono text-[11px]`}>
                  {type === "ssl" ? u.remote_ip : u.remote_gw_ip}
                </td>
                <td className={`${cell} font-mono text-[11px]`}>
                  {type === "ssl" ? u.vpn_ip : u.assigned_ip}
                </td>
                <td className={`${cell} text-right font-mono`}>{formatBytes(u.bytes_in)}</td>
                <td className={`${cell} text-right font-mono`}>{formatBytes(u.bytes_out)}</td>
                <td className={`${cell} font-mono text-[11px]`}>{u.device}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function SessionHistoryTable({
  items, loading, error,
}: {
  items: VPNSessionHistoryItem[] | null; loading: boolean; error: boolean;
}) {
  const cell = "py-2 px-3";

  if (error) {
    return <p className="text-sm text-destructive text-center py-6">Failed to load session history</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b bg-muted/50 text-muted-foreground">
            <th className={`text-left ${cell}`}>Username</th>
            <th className={`text-left ${cell}`}>Type</th>
            <th className={`text-left ${cell}`}>Session Started</th>
            <th className={`text-left ${cell}`}>Last Seen</th>
            <th className={`text-right ${cell}`}>Bytes In</th>
            <th className={`text-right ${cell}`}>Bytes Out</th>
            <th className={`text-left ${cell}`}>Status</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <tr key={i} className="border-b animate-pulse">
                {[1, 2, 3, 4, 5, 6, 7].map((j) => (
                  <td key={j} className={cell}><div className="h-3 bg-muted rounded" /></td>
                ))}
              </tr>
            ))
          ) : !items || items.length === 0 ? (
            <tr>
              <td colSpan={7} className="py-10 text-center text-muted-foreground">
                No VPN sessions in this range
              </td>
            </tr>
          ) : (
            items.map((h, i) => (
              <tr key={i} className="border-b last:border-0 hover:bg-muted/30">
                <td className={`${cell} font-medium font-mono`}>{h.username}</td>
                <td className={`${cell}`}>
                  <span className={cn(
                    "inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold",
                    h.protocol === "SSL VPN"
                      ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400"
                      : "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400"
                  )}>
                    {h.protocol === "SSL VPN" ? "SSL" : "IPsec"}
                  </span>
                </td>
                <td className={`${cell} font-mono text-[11px]`}>{fmtTs(h.session_started)}</td>
                <td className={`${cell} font-mono text-[11px]`}>{fmtTs(h.last_seen)}</td>
                <td className={`${cell} text-right font-mono`}>{formatBytes(h.bytes_in)}</td>
                <td className={`${cell} text-right font-mono`}>{formatBytes(h.bytes_out)}</td>
                <td className={`${cell}`}>
                  {h.status === "active" ? (
                    <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400">
                      <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                      Active
                    </span>
                  ) : (
                    <span className="text-muted-foreground">Ended</span>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
