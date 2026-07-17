/**
 * TypeScript type definitions matching backend Pydantic schemas.
 */

// ── API Envelope ───────────────────────────────────────────────

export interface ResponseMeta {
  total?: number;
  page?: number;
  page_size?: number;
  query_took_ms?: number;
  search_after?: (string | number)[] | null;  // cursor for search_after pagination
  /**
   * Set when an underlying query timed out, tripped the cluster circuit breaker, or
   * returned partial shard results. The values in `data` are then incomplete or
   * zeroed — a 0 means "unknown", NOT "no traffic". Render the data as unavailable
   * rather than presenting it as a measurement.
   */
  degraded?: boolean | null;
  partial_errors?: string[] | null;
}

export interface APIResponse<T = unknown> {
  success: boolean;
  data: T | null;
  meta: ResponseMeta | null;
  error: {
    code: string;
    message: string;
  } | null;
}

// ── Auth ───────────────────────────────────────────────────────

export interface LoginRequest {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface User {
  id: string;
  username: string;
  email: string;
  full_name: string;
  role: "superadmin" | "admin" | "operator" | "viewer";
  is_active: boolean;
  must_change_password: boolean;
  last_login: string | null;
  created_at: string;
  updated_at: string;
}

// ── Overview Dashboard ─────────────────────────────────────────

export interface ActiveUserKPI {
  active_users: number;
  label: string;
}

export interface SparklinePoint {
  timestamp: number;
  value: number;
}

export interface DeviceResourceStatus {
  device: string;
  hostname?: string;
  serial_number?: string;
  cpu_usage?: number;
  mem_usage?: number;
  session_count?: number;
  sync_status?: string;
  mem_capacity_kb?: number;
  session_sparkline?: SparklinePoint[];
}

export interface TopApplication {
  application: string;
  total_bytes: number;
  bytes_human: string;
}

export interface WanLinkStatus {
  link: string;
  link_name: string;
  status: string;
}

export interface SiteWanStatus {
  site: string;
  device?: string;
  links: WanLinkStatus[];
}

export interface ThroughputKPI {
  total_bytes: number;
  bytes_human: string;
}

export interface OverviewData {
  ssl_vpn_users: ActiveUserKPI;
  ipsec_vpn_users: ActiveUserKPI;
  fortigate_device_count: number;
  devices: DeviceResourceStatus[];
  top_applications: TopApplication[];
  top_dst_as_orgs: TopASOrg[];
  sdwan_sites: SiteWanStatus[];
  total_throughput: ThroughputKPI;
  ha_status?: HAStatusKPI;
  wan_bandwidth: SiteWanBandwidth[];
  inbound_vip_services: TopInboundService[];
  active_alert_count: number;
}

export interface TopASOrg {
  org_name: string;
  total_bytes: number;
  bytes_human: string;
}

export interface HAStatusKPI {
  ha_mode: string;
  member_count: number;
  overall_health: string;
}

export interface WanInterfaceSummary {
  label: string;
  in_mbps?: number;
  out_mbps?: number;
  speed_mbps?: number;
  oper_status?: string;
}

export interface SiteWanBandwidth {
  site: string;
  interfaces: WanInterfaceSummary[];
}

export interface TopInboundService {
  service_name: string;
  total_bytes: number;
  bytes_human: string;
}

// ── Traffic Flow v2.0 (PRD) ──────────────────────────────────────

export interface TrafficFlowAppItem { app_name: string; total_bytes: number; speed_mbps: number; percentage: number; }
export interface TrafficFlowCategoryItem { category_name: string; total_bytes: number; count: number; }
export interface TrafficFlowASOrgItem { org_name: string; total_bytes: number; }
export interface TrafficFlowASCountryItem { country: string; total_bytes: number; flag_code: string; }
export interface TrafficFlowClientItem { ip: string; total_bytes: number; upload_bytes?: number; download_bytes?: number; }
export interface TrafficFlowServerItem { ip: string; total_bytes: number; upload_bytes?: number; download_bytes?: number; hostname: string; }
export interface TrafficFlowProtocolItem { protocol: string; total_bytes: number; percentage: number; }
export interface TrafficFlowEgressItem { interface: string; total_bytes: number; }
export interface TrafficFlowSrcASOrgItem { org_name: string; total_bytes: number; }
export interface TrafficFlowSummary {
  total_bytes: number;
  total_upload: number;
  total_download: number;
  total_sessions: number;
  top_apps: TrafficFlowAppItem[];
  top_src_as_org: TrafficFlowSrcASOrgItem[];
  app_categories: TrafficFlowCategoryItem[];
  top_dst_as_org: TrafficFlowASOrgItem[];
  top_dst_as_country: TrafficFlowASCountryItem[];
  top_clients: TrafficFlowClientItem[];
  top_servers: TrafficFlowServerItem[];
  protocol_dist: TrafficFlowProtocolItem[];
  egress_breakdown: TrafficFlowEgressItem[];
  ingress_breakdown: TrafficFlowEgressItem[];
}
export interface TrafficFlowChartData { chart_data: Record<string, any>[]; app_names: string[]; global_speed_by_app?: Record<string, number>; }
export interface TrafficFlowTableRecord { client_ip: string; server_ip: string; app_name: string; bytes: number; upload_bytes?: number; download_bytes?: number; packets: number; sessions: number; }
export interface TrafficFlowTableData { records: TrafficFlowTableRecord[]; after_key: any; }

// ── Raw Flow Record ─────────────────────────────────────────────

export interface RawFlowRecord {
  timestamp: string;
  client_ip: string;
  server_ip: string;
  application: string;
  category: string;
  classification?: string;
  protocol: string;
  dst_port: number;
  total_bytes: number;
  bytes_human?: string;
  packets: number;
  ingress_interface: string;
  egress_interface: string;
  correlation_id?: string;
  correlation_direction?: string;
}

// ── Interface Stats v2.0 ─────────────────────────────────────────

export interface InterfaceTimelinePoint { timestamp: number; in_mbps: number | null; out_mbps: number | null; }
export interface InterfaceStatsItem { if_index: string; if_name: string; label: string; current_in_mbps: number | null; current_out_mbps: number | null; speed_mbps: number | null; oper_status: number | null; timeline: InterfaceTimelinePoint[]; }
export interface InterfaceStatsData { interfaces: InterfaceStatsItem[]; }

// ── HA Status ────────────────────────────────────────────────────

export interface HAMember { memberIndex: number; role: string; syncStatus: string; priority: number; hostname: string; }
export interface HAStatusData { ha_mode: string; members: HAMember[]; overallHealth: string; message?: string; }

// ── SD-WAN ─────────────────────────────────────────────────────

export interface LinkMetricPoint {
  timestamp: number;
  value: number;
  label: string;
  link_type: string; // "WAN" or "MPLS"
}

export interface SLATimeline {
  links: LinkMetricPoint[];
}

export interface LinkCurrentStatus {
  link: string;
  ifname: string;
  label: string;
  link_type: string;
  status: string;
  sla_target: string;
}

export interface SiteSLAStatus {
  site: string;
  device?: string;
  links: LinkCurrentStatus[];
}

export interface SLASummary {
  avg_latency: number[];
  max_latency: number[];
  avg_jitter: number[];
  avg_packet_loss: number[];
  labels: string[];
  link_types: string[];
}

export interface SDWANData {
  latency_timeline: SLATimeline;
  jitter_timeline: SLATimeline;
  packet_loss_timeline: SLATimeline;
  link_status: SiteSLAStatus[];
  summary: SLASummary;
}

export interface TimePoint {
  timestamp: number;
  value: number;
}

// ── Resources ──────────────────────────────────────────────────

export interface ResourceData {
  timeline: {
    cpu: { timestamp: number; value: number; device: string }[];
    memory: { timestamp: number; value: number; device: string }[];
    sessions: { timestamp: number; value: number; device: string }[];
  };
  current: DeviceResourceStatus[];
}

// ── Alerts ─────────────────────────────────────────────────────

export interface AlertRule {
  id: string;
  name: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  kind: "single" | "composite";
  notify_when: "any" | "all";
  clauses: Record<string, unknown>[];
  template_id: string | null;
  data_source: string;
  metric_field: string;
  aggregation: string;
  condition: string;
  threshold_value: number;
  evaluation_window_minutes: number;
  sustained_for_minutes: number;
  notify_channels: string[];
  site_name: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

// ── Alert Templates (v3 §3.12) ─────────────────────────────────

export interface AlertTemplate {
  id: string;
  name: string;
  category: string;
  icon: string;
  description: string;
  underlying_kind: string;
  locked_fields: Record<string, unknown>;
  exposed_fields: string[];
  is_user_created: boolean;
  created_at: string;
}

// ── Notification Configs (v3 §3.13) ─────────────────────────────

export type NotificationChannelName = "telegram" | "email" | "whatsapp";

export interface NotificationChannelRead {
  channel: NotificationChannelName;
  enabled: boolean;
  min_severity: string;
  config: Record<string, unknown>;   // masked on GET
  recipients: Record<string, unknown> | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

// ── Maintenance Windows (v3 §3.14) ──────────────────────────────

export interface MaintenanceWindow {
  id: string;
  site_name: string;
  starts_at: string;
  ends_at: string;
  reason: string;
  created_by: string | null;
  created_at: string;
}

// ── Notifications ──────────────────────────────────────────────

export interface Notification {
  id: string;
  alert_name: string;
  severity: string;
  message: string;
  is_read: boolean;
  created_at: string;
}

// ── Time Range ─────────────────────────────────────────────────

export interface TimeRange {
  gte_ms: number;
  lte_ms: number;
  label: string;
}

export const TIME_PRESETS: TimeRange[] = [
  { gte_ms: 0, lte_ms: 0, label: "Last 15 minutes" },
  { gte_ms: 0, lte_ms: 0, label: "Last 1 hour" },
  { gte_ms: 0, lte_ms: 0, label: "Last 2 hours" },
  { gte_ms: 0, lte_ms: 0, label: "Last 4 hours" },
  { gte_ms: 0, lte_ms: 0, label: "Last 12 hours" },
  { gte_ms: 0, lte_ms: 0, label: "Last 24 hours" },
].map((p) => {
  const secs: Record<string, number> = {
    "15": 15 * 60,
    "1": 3600,
    "2": 7200,
    "4": 14400,
    "12": 43200,
    "24": 86400,
  };
  const match = p.label.match(/(\d+)/);
  const num = match ? Number(match[1]) : 15;
  const key = match?.[0] || "15";
  return {
    ...p,
    gte_ms: Date.now() - secs[key] * 1000,
    lte_ms: Date.now(),
    label: p.label,
  };
});

// ── Sankey Diagram (d3-sankey) ───────────────────────────────────

export interface SankeyNode { id: number; label: string; level: number; }
export interface SankeyLink { source: number; target: number; value: number; }
export interface SankeyResponse { nodes: SankeyNode[]; links: SankeyLink[]; }

/** Sankey node with layout-computed properties (after sankey.layout()). */
export interface SankeyNodeExt extends SankeyNode {
  x0?: number; x1?: number; y0?: number; y1?: number;
  value?: number; index?: number; depth?: number; height?: number;
  sourceLinks?: Array<SankeyLinkExt>;
  targetLinks?: Array<SankeyLinkExt>;
}

/** Sankey link with layout-computed properties (after sankey.layout()). */
export interface SankeyLinkExt extends Omit<SankeyLink, "source" | "target"> {
  width?: number; y0?: number; y1?: number; index?: number;
  source: SankeyNodeExt | number;
  target: SankeyNodeExt | number;
}

// ── Traffic Inbound v2.0 ──────────────────────────────────────────

export interface TrafficInboundServiceItem { service_name: string; service_port: number | string; total_bytes: number; speed_mbps: number; percentage: number; }
export interface TrafficInboundSummary {
  total_bytes: number;
  total_upload: number;
  total_download: number;
  total_sessions: number;
  top_services: TrafficInboundServiceItem[];
  service_categories?: { category_name: string; total_bytes: number; count: number }[];
  top_dst_as_org?: TrafficFlowASOrgItem[];
  top_dst_as_country?: TrafficFlowASCountryItem[];
  top_src_as_org: TrafficFlowASOrgItem[];
  top_src_as_country: TrafficFlowASCountryItem[];
  top_clients: TrafficFlowClientItem[];
  top_servers: TrafficFlowServerItem[];
  protocol_dist: { protocol: string; total_bytes: number }[];
  egress_breakdown: TrafficFlowEgressItem[];
  ingress_breakdown: TrafficFlowEgressItem[];
}
export interface TrafficInboundChartData { chart_data: Record<string, any>[]; service_names: string[]; }
export interface TrafficInboundTableRecord { client_ip: string; server_ip: string; service: string; bytes: number; upload_bytes?: number; download_bytes?: number; packets: number; sessions: number; }
export interface TrafficInboundTableData { records: TrafficInboundTableRecord[]; after_key: any; total: number; }

// ── Traffic Internal v2.0 ──────────────────────────────────────────

export interface TrafficInternalSummary {
  total_bytes: number;
  total_upload: number;
  total_download: number;
  total_sessions: number;
  top_services: TrafficInboundServiceItem[];
  top_clients: TrafficFlowClientItem[];
  top_servers: TrafficFlowServerItem[];
  ingress_breakdown: { interface: string; total_bytes: number }[];
  egress_breakdown: TrafficFlowEgressItem[];
  protocol_dist: { protocol: string; total_bytes: number }[];
}
export interface TrafficInternalChartData { chart_data: Record<string, any>[]; service_names: string[]; }
export interface TrafficInternalTableRecord { client_ip: string; server_ip: string; service: string; bytes: number; upload_bytes?: number; download_bytes?: number; packets: number; sessions: number; }
export interface TrafficInternalTableData { records: TrafficInternalTableRecord[]; after_key: any; }