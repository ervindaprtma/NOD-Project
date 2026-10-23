// API DTOs for the Network Devices page (mirrors backend app/api/network_devices.py).

export type DeviceState = "up" | "down" | "admin_down";

export interface InterfaceCounts {
  up: number;
  down: number;
  admin_down: number;
  total: number;
}

export interface DeviceRow {
  source_ip: string;
  vendor: string; // fortigate | h3c | cisco
  site: string | null;
  name: string;
  online: boolean;
  last_seen_ms: number;
  cluster: string;
  counts: InterfaceCounts;
}

export interface InterfaceBox {
  if_name: string;
  ifindex: string | null;
  state: DeviceState;
  oper: string | null;
  admin: string | null;
  speed_mbps: number | null;
  description: string | null;
}

export interface DeviceDetailData extends DeviceRow {
  interfaces: InterfaceBox[];
}

export interface SummaryData {
  devices_total: number;
  devices_online: number;
  interfaces_up: number;
  interfaces_down: number;
  interfaces_admin_down: number;
  by_site: Record<string, DeviceRow[]>;
}

export interface ChartPoint {
  ts_ms: number;
  in: number;
  out: number;
  util_pct?: number | null;
}

export interface ChartData {
  metric: "mb" | "mbps";
  interval: string;
  speed_mbps: number | null;
  points: ChartPoint[];
}

export interface StateTransition {
  at_ms: number;
  if_name: string;
  from: string;
  to: string;
  duration_before_ms: number;
  offline_gap: boolean;
}

export interface HistoryData {
  interval: string;
  transitions: StateTransition[];
}

// Standard API envelope.
export interface Envelope<T> {
  data: T;
  meta?: { query_took_ms?: number; degraded?: boolean };
}

// ── Shared presentational helpers ──
export const VENDOR_LABEL: Record<string, string> = {
  fortigate: "FortiGate",
  h3c: "H3C",
  cisco: "Cisco",
};

// state → tailwind classes for the interface boxes (plan: 🟩 up / 🟥 down / ⬜ admin).
export const STATE_BOX: Record<DeviceState, string> = {
  up: "border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  down: "border-red-500/60 bg-red-500/10 text-red-700 dark:text-red-300",
  admin_down: "border-zinc-400/50 bg-zinc-400/10 text-zinc-500 dark:text-zinc-400",
};

export const STATE_DOT: Record<DeviceState, string> = {
  up: "bg-emerald-500",
  down: "bg-red-500",
  admin_down: "bg-zinc-400",
};

// device site (DC/DRC/Office) → the FGT resource endpoint's site_name.
export const FGT_SITE_NAME: Record<string, string> = {
  DC: "Site_FGT-DC",
  DRC: "Site_FGT-DRC",
  Office: "Site_FGT_Office",
};

export function fmtSpeed(mbps: number | null): string {
  if (!mbps || mbps <= 0) return "—";
  if (mbps >= 1000) return `${(mbps / 1000).toFixed(mbps % 1000 ? 1 : 0)}G`;
  return `${mbps}M`;
}

// ── Topology (plan §4) ──
export interface TopologyNode {
  id: string;
  source_ip: string | null;
  support: boolean;
  display_name: string | null;
  icon_kind: string; // fg | sw | fw | router | cloud | srv | ap | custom
  icon_id: string | null;
  icon_url?: string | null; // resolved uploaded-icon URL
  x: number;
  y: number;
  rotation?: number; // degrees clockwise (text annotation nodes); 0 = upright
  online: boolean | null; // support nodes: null (never state-coloured)
}

export interface IconData {
  id: string;
  name: string;
  url: string;
}

export interface TopologyLink {
  id: string;
  a_node_id: string;
  a_if_name: string | null;
  b_node_id: string;
  b_if_name: string | null;
  link_type: "lan" | "wan" | "mpls" | "wireless";
  color?: string | null; // optional per-link stroke colour (hex); null = default muted
  curve_dx?: number; // Bézier control-point offset from midpoint (canvas px); 0 = straight
  curve_dy?: number;
  a_oper: string | null;
  b_oper: string | null;
  rx_mbps: number;
  tx_mbps: number;
  // Per-end interface rates (null = support/no-data end). rx = IN/download, tx = OUT/upload.
  a_rx_mbps?: number | null;
  a_tx_mbps?: number | null;
  b_rx_mbps?: number | null;
  b_tx_mbps?: number | null;
}

export interface TopologyData {
  nodes: TopologyNode[];
  links: TopologyLink[];
}

export const ICON_GLYPH: Record<string, string> = {
  fg: "🛡", sw: "🔀", fw: "🧱", router: "📡", cloud: "☁", srv: "🖥", ap: "📶", custom: "⬡",
};

// Built-in default icons offered in the Add/Edit Device dialog (kind → DeviceIcon SVG).
export const DEFAULT_ICONS: { kind: string; label: string }[] = [
  { kind: "pc", label: "PC / Workstation" },
  { kind: "ap", label: "Access Point" },
  { kind: "router", label: "Router" },
  { kind: "fw", label: "Firewall" },
  { kind: "cloud", label: "Cloud / Internet" },
  { kind: "srv_tower", label: "Server (Tower)" },
  { kind: "srv_rack", label: "Server (Rackmount)" },
  { kind: "sw_l2", label: "Switch L2" },
  { kind: "sw_l3", label: "Switch L3" },
];

// link_type → svg stroke style (v1 hand-rolled glyphs; d3 markers are the upgrade path).
export const LINK_STYLE: Record<string, { width: number; dash?: string }> = {
  lan: { width: 1.5 },
  wan: { width: 4 },
  mpls: { width: 2, dash: "7 4" },
  wireless: { width: 2, dash: "2 4" },
};

export function fmtRate(mbps: number): string {
  if (!mbps || mbps <= 0) return "0 Mbps";
  if (mbps >= 1000) return `${(mbps / 1000).toFixed(1)} Gbps`;
  if (mbps >= 1) return `${mbps.toFixed(0)} Mbps`;
  return `${(mbps * 1000).toFixed(0)} Kbps`;
}

export function fmtDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}
