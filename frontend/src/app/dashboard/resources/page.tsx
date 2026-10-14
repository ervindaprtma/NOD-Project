"use client";

import { useState, useEffect, useCallback } from "react";
import useSWR from "swr";
import { swrFetcher } from "@/lib/api";
import { TAB_TRIGGER_CLASS, REFRESH_INTERVALS, DEFAULT_REFRESH_MS } from "@/lib/constants";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@radix-ui/react-tabs";
import type { DeviceRow, Envelope, SummaryData } from "@/components/network-devices/types";
import { OverviewCards } from "@/components/network-devices/OverviewCards";
import { DeviceList } from "@/components/network-devices/DeviceList";
import { DeviceDetail } from "@/components/network-devices/DeviceDetail";
import { TopologyCanvas } from "@/components/network-devices/TopologyCanvas";
import { InterfaceBandwidth } from "@/components/network-devices/InterfaceBandwidth";
import { AvailabilityCards } from "@/components/network-devices/AvailabilityCards";
import { FortiGateResources } from "@/components/network-devices/FortiGateResources";

type TabKey = "overview" | "devices" | "bandwidth" | "topology";
const TABS: TabKey[] = ["overview", "devices", "bandwidth", "topology"];

// Legacy deep-links from the Overview page / bookmarks used the old Resources params
// (?site=Site_FGT-* , ?tab=availability|bandwidth|resources). Honour them (plan §1):
// availability → Overview; a site (any FGT tab) → that FortiGate's device detail.
const LEGACY_FGT_SOURCE: Record<string, string> = {
  "Site_FGT-DC": "10.80.150.1",
  "Site_FGT-DRC": "10.90.150.1",
  "Site_FGT_Office": "10.70.150.1",
};

function translateLegacy(p: URLSearchParams): { tab: TabKey; device: string | null } | null {
  const legacySite = p.get("site");
  const legacyTab = p.get("tab");
  const isLegacyTab = legacyTab === "availability" || legacyTab === "bandwidth" || legacyTab === "resources";
  if (!legacySite && !isLegacyTab) return null;
  if (legacyTab === "availability") return { tab: "overview", device: null };
  if (legacyTab === "bandwidth") return { tab: "bandwidth", device: null };  // WAN/MPLS view
  const device = legacySite ? LEGACY_FGT_SOURCE[legacySite] ?? null : null;
  return { tab: device ? "devices" : "overview", device };
}

// Network Devices page (plan v3 §6) — merged onto the old /dashboard/resources route.
// Tabs: Overview (fleet KPIs + site cards), Devices (site-grouped list → detail),
// Topology (steps 8–9). Current state comes from a fixed backend snapshot window;
// the per-device window selector drives charts/history inside the detail view.
export default function NetworkDevicesPage() {
  const [tab, setTab] = useState<TabKey>("overview");
  const [device, setDevice] = useState<string | null>(null);
  // Auto-refresh cadence for every tab (Off/15s/30s/60s), same control as the Traffic pages.
  const [refreshMs, setRefreshMs] = useState(DEFAULT_REFRESH_MS);

  // Hydrate from URL after mount (reading it in a useState initializer mismatches SSR).
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const legacy = translateLegacy(p);
    if (legacy) {
      setTab(legacy.tab);
      setDevice(legacy.device);
      const np = new URLSearchParams();
      np.set("tab", legacy.tab);
      if (legacy.device) np.set("device", legacy.device);
      window.history.replaceState(null, "", `${window.location.pathname}?${np.toString()}`);
      return;
    }
    const t = p.get("tab");
    if (t && (TABS as string[]).includes(t)) setTab(t as TabKey);
    const d = p.get("device");
    if (d) setDevice(d);
  }, []);

  // Keep the URL in sync so deep-links / refresh preserve state (replace, no history spam).
  const syncUrl = useCallback((t: TabKey, d: string | null) => {
    const p = new URLSearchParams(window.location.search);
    p.set("tab", t);
    if (d) p.set("device", d);
    else p.delete("device");
    window.history.replaceState(null, "", `${window.location.pathname}?${p.toString()}`);
  }, []);

  const go = useCallback(
    (t: TabKey, d: string | null) => {
      setTab(t);
      setDevice(d);
      syncUrl(t, d);
    },
    [syncUrl],
  );

  const openDevice = useCallback((source: string) => go("devices", source), [go]);

  const { data: summaryResp, isLoading: summaryLoading } = useSWR<Envelope<SummaryData>>(
    "/api/v1/network-devices/summary",
    swrFetcher,
    { refreshInterval: refreshMs },
  );
  const { data: devicesResp, isLoading: devicesLoading } = useSWR<Envelope<DeviceRow[]>>(
    "/api/v1/network-devices/devices",
    swrFetcher,
    { refreshInterval: refreshMs },
  );

  const summary = summaryResp?.data;
  const devices = devicesResp?.data || [];

  return (
    <div className="p-4 md:p-6 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Network Devices</h1>
        {/* Auto-refresh selector — applies to every tab (same control as Traffic pages). */}
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

      <Tabs value={tab} onValueChange={(v) => go(v as TabKey, v === "devices" ? device : null)}>
        <TabsList className="mb-4 p-1 gap-1 bg-muted/40 dark:bg-muted/30 rounded-lg inline-flex">
          <TabsTrigger value="overview" className={TAB_TRIGGER_CLASS}>Overview</TabsTrigger>
          <TabsTrigger value="devices" className={TAB_TRIGGER_CLASS}>Devices</TabsTrigger>
          <TabsTrigger value="bandwidth" className={TAB_TRIGGER_CLASS}>Interface Bandwidth</TabsTrigger>
          <TabsTrigger value="topology" className={TAB_TRIGGER_CLASS}>Topology</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          {/* Device Availability pinned to the top, then FortiGate resources, then fleet cards */}
          <AvailabilityCards refreshMs={refreshMs} />
          <FortiGateResources refreshMs={refreshMs} />
          {summaryLoading ? (
            <p className="text-sm text-muted-foreground py-8 text-center">Loading fleet…</p>
          ) : summary ? (
            <OverviewCards summary={summary} onSelect={openDevice} />
          ) : (
            <p className="text-sm text-muted-foreground py-8 text-center">No device data.</p>
          )}
        </TabsContent>

        <TabsContent value="devices">
          {device ? (
            <DeviceDetail source={device} onBack={() => go("devices", null)} refreshMs={refreshMs} />
          ) : devicesLoading ? (
            <p className="text-sm text-muted-foreground py-8 text-center">Loading devices…</p>
          ) : (
            <DeviceList devices={devices} onSelect={openDevice} />
          )}
        </TabsContent>

        <TabsContent value="bandwidth">
          <InterfaceBandwidth refreshMs={refreshMs} />
        </TabsContent>

        <TabsContent value="topology">
          <TopologyCanvas onOpenDevice={openDevice} refreshMs={refreshMs} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
