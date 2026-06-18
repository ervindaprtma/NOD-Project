# Network Observability Dashboard (NOD)
## PRD — Updated Sections: Version 1.0.0 → 1.1.0

**Version:** 1.1.0  
**Status:** Draft — Pending Engineering Review  
**Effective Date:** June 2026  
**Classification:** Internal — Confidential  
**Previous Version:** 1.0.0  
**Change Author:** Principal Architect

---

> **INSTRUKSI PENGGANTIAN DOKUMEN**
>
> File ini berisi penggantian penuh untuk **Section 1.3, 5, 6 (FR-01, FR-02, FR-17 saja), 9, 10.3**, dan **Appendix B** dari NOD PRD v1.0.0.
> Salin setiap bagian di bawah dan gantikan seluruh konten section yang bersesuaian di dokumen utama.
> FR-03 s.d. FR-16 **tidak berubah** dan dipertahankan dari v1.0.0.

---

## Section 1.3 — Scope (Revised)
> **Replaces Section 1.3 in its entirety.**

**In Scope:**
- FortiGate AppID NetFlow/IPFIX traffic flow analytics — sub-structured into five dedicated analytical sub-pages: Summary, Timeseries Analytics (Mbps), Custom Filter, Sankey Diagram, and Traffic Details (Conversations)
- SD-WAN SLA performance monitoring (latency, jitter, packet loss per link)
- FortiGate HA and resource monitoring (CPU, memory, session count, sync status)
- SSL VPN and IPsec VPN user session visibility, including an Active VPN Users consolidated table on the Overview Dashboard
- SNMP Interface Statistics visualization for FortiGate interfaces (interface utilization %, link status Up/Down, error/discard counters) — data pending pipeline validation per Section 5.4
- Application-level throughput analytics expressed in Mbps via interactive, brushable TSVB-equivalent Stacked Area Charts
- Conversation-level traffic detail view with session-count aggregation
- Threshold-based alerting via Telegram Bot API and SMTP Email
- Structured report generation in PDF, HTML, and DOCX formats
- Report distribution via Email, Telegram, WhatsApp, Discord, and direct download
- Role-based user management (superadmin, admin, operator, viewer)
- Full containerized deployment via `docker-compose`

**Out of Scope:**
- FortiGate device configuration or policy management
- Modification of the OpenSearch ingestion pipeline or index templates
- OpenSearch cluster administration
- Raw syslog ingestion

---

---

## Section 5 — Data Dictionary (Revised)
> **Replaces Section 5 in its entirety.**

> **Scope:** Field definitions are derived from JSON document samples provided in the project brief, supplemented in v1.1.0 by four pipeline-dependent fields added to support new features defined in FR-02 (Sub-pages 2.4 and 2.5) and FR-01 (Panel P01-K). Fields marked **[NO-AGG]** must not be used as aggregation targets. Fields marked **[⚠ REQUIRES PIPELINE VALIDATION]** are provisional definitions that must be confirmed against the live ingest pipeline before any production query referencing them is written or merged.

---

### 5.1 AppID Flow Index — `fortigate-appid-flow-YYYY.MM.DD`

**Routing:** `OPENSEARCH_APPID_URL`  
**Timestamp field:** `@timestamp`

| Field | Mapping Type | Description | Example |
|-------|-------------|-------------|---------|
| `flow.src.ip.addr` | `ip` | Raw source IP address (direction-agnostic; pre-semantic-layer) | `192.168.100.69` |
| `flow.dst.ip.addr` | `ip` | Raw destination IP address (direction-agnostic) | `52.97.112.210` |
| `flow.dst.l4.port.id` | `integer` | Destination Layer 4 port number | `443` |
| `l4.proto.name` | `keyword` | Layer 4 protocol name | `UDP`, `TCP` |
| `flow.client.ip.addr` | `ip` | **Semantic client IP** — derived by the upstream parser via four-level priority stack (zone, NAT, RFC1918, port heuristics) | `192.168.100.69` |
| `flow.server.ip.addr` | `ip` | **Semantic server IP** — counterpart to `flow.client.ip.addr` | `52.97.112.210` |
| `flow.client.bytes` | `long` | Bytes attributed to the client-direction of the flow | `10806` |
| `flow.server.bytes` | `long` | Bytes attributed to the server-direction of the flow | `0` |
| `flow.application.name` | `keyword` | FortiGate DPI-identified application name | `Microsoft.Office.365.Portal` |
| `flow.application.category` | `keyword` | FortiGate application category | `Collaboration` |
| `flow.bytes_human` | `keyword` | **[NO-AGG]** Pre-formatted total bytes string; display only | `10.55 KB` |
| `flow.packets` | `long` | Total packet count for the flow record | `16` |
| `flow.in.netif.name` | `keyword` | Ingress network interface or zone name | `Infrastructure` |
| `flow.out.netif.name` | `keyword` | Egress network interface name | `port15` |
| `flow.out.netif.descr` | `keyword` | Egress interface human-readable description (e.g., WAN link label) | `WAN Internet` |
| `flow.as_country` | `keyword` | **[⚠ REQUIRES PIPELINE VALIDATION]** ISO 3166-1 alpha-2 country code of the destination Autonomous System, enriched at ingest. Must be confirmed present in the ingest pipeline before production use. Used by FR-02.4 (Sankey Diagram — 4th node: AS Country). | `US` |
| `flow.destination_organization` | `keyword` | **[⚠ REQUIRES PIPELINE VALIDATION]** Organization or ISP name of the destination address, enriched at ingest via ASN/WHOIS lookup. Must be confirmed present before production use. Used by FR-01 Panel P01-K. | `Google LLC` |
| `conversation_id` | `keyword` | **[⚠ REQUIRES PIPELINE VALIDATION]** Stable, pipeline-derived identifier for a bidirectional flow conversation — typically a deterministic hash of the normalized 5-tuple (client IP, server IP, client port, server port, protocol). If absent at query time, the Sub-page 2.5 Conversations view must render a graceful degraded state and this field must not appear in any production query until confirmed. | `a3f82b7e9c1d` |

> **Critical Note — `flow.bytes_human`:** This is a pre-rendered keyword string and **must not** be used in any aggregation query. Byte-sum calculations must operate on `flow.client.bytes` + `flow.server.bytes`.

> **v1.1.0 Note — Pipeline-Dependent Fields:** The three fields above (`flow.as_country`, `flow.destination_organization`, `conversation_id`) are **provisional definitions** introduced to support FR-02.4, FR-02.5, and FR-01 P01-K. Pipeline engineering must validate that these fields exist, are correctly indexed as `keyword` type, and contain populated data before any feature that references them is released to production. Features dependent on absent fields must render graceful empty states rather than return query errors.

> **v1.1.0 Note — `sessions` (Aggregation-Derived Metric):** The "Sessions" column in FR-02.5 (Traffic Details — Conversations) represents the **count of flow documents** grouped under a given `conversation_id` bucket. It is **not** a stored field and requires no ingest pipeline validation. It is computed at query time as a `value_count` aggregation on `@timestamp` (or `_id`) within each `terms` aggregation bucket on `conversation_id`.

---

### 5.2 SNMP / Telegraf Index — `telegraf-index*`

**Routing:** `OPENSEARCH_TELEGRAF_URL`  
**Timestamp field:** `@timestamp`  
**Discriminator field:** `measurement_name` — every query against this index **must** include an exact `term` filter on this field.

---

#### 5.2.1 SD-WAN SLA — `measurement_name: Site_<SITE_NAME>`

The site-specific metrics are nested under a top-level key that **equals** the `measurement_name` value (e.g., `"Site_FGT-DC":{...}`). Backend query builders must template this key dynamically from the configured site name list.

| Field Path | Mapping Type | Description | Example |
|-----------|-------------|-------------|---------|
| `measurement_name` | `keyword` | Measurement discriminator | `Site_FGT-DC` |
| `<site>.ifname_link1` | `keyword` | WAN interface name for link 1 | `wan1` |
| `<site>.ifname_link2` | `keyword` | WAN interface name for link 2 | `wan2` |
| `<site>.jitter_link1` | `float` | Measured jitter on link 1 (ms) | `0.037` |
| `<site>.jitter_link2` | `float` | Measured jitter on link 2 (ms) | `0.025` |
| `<site>.latency_link1` | `float` | Round-trip latency on link 1 (ms) | `13.421` |
| `<site>.latency_link2` | `float` | Round-trip latency on link 2 (ms) | `13.095` |
| `<site>.name_sla_sdwan_1` | `keyword` | **[NO-AGG]** SLA probe target name for link 1 | `ping google.com` |
| `<site>.name_sla_sdwan_2` | `keyword` | **[NO-AGG]** SLA probe target name for link 2 | `ping google.com` |
| `<site>.packet_loss_link1` | `float` | Packet loss percentage on link 1 | `0` |
| `<site>.packet_loss_link2` | `float` | Packet loss percentage on link 2 | `0` |
| `<site>.status_link1` | `integer` | Link 1 status: `0` = Up, non-zero = Degraded/Down | `0` |
| `<site>.status_link2` | `integer` | Link 2 status: `0` = Up, non-zero = Degraded/Down | `0` |

---

#### 5.2.2 HA & Resource Monitoring — `measurement_name: ha_member`

| Field Path | Mapping Type | Description | Example |
|-----------|-------------|-------------|---------|
| `measurement_name` | `keyword` | Measurement discriminator | `ha_member` |
| `ha_member.cpu_usage` | `integer` | CPU utilization percentage | `1` |
| `ha_member.mem_usage` | `integer` | Memory utilization percentage | `43` |
| `ha_member.session_count` | `long` | Active firewall session count | `20457` |
| `ha_member.sync_status` | `integer` | HA synchronization status: `1` = In Sync | `1` |
| `tag.device` | `keyword` | Device logical identifier | `FGT-DC` |
| `tag.hostname` | `keyword` | FortiGate OS hostname | `FG_DC_GTN-01` |
| `tag.serial_number` | `keyword` | **[NO-AGG]** Hardware serial number | `FG200ETK20904635` |

---

#### 5.2.3 SSL VPN Sessions — `measurement_name: Site_<SITE_NAME>_SSLVPN`

The SSLVPN metrics are nested under a key matching the full `measurement_name` value (e.g., `"Site_FGT-DC_SSLVPN":{...}`).

| Field Path | Mapping Type | Description | Example |
|-----------|-------------|-------------|---------|
| `measurement_name` | `keyword` | Measurement discriminator | `Site_FGT-DC_SSLVPN` |
| `<site_SSLVPN>.bytes_in` | `long` | Bytes received by the VPN endpoint in this session | `53358` |
| `<site_SSLVPN>.bytes_out` | `long` | Bytes sent from the VPN endpoint in this session | `126869` |
| `<site_SSLVPN>.remote_ip` | `ip` | Client's public-facing IP address | `103.156.134.50` |
| `<site_SSLVPN>.vpn_ip` | `ip` | Assigned SSL VPN tunnel IP address | `10.80.148.171` |
| `tag.device` | `keyword` | FortiGate device identifier | `FGT-DC` |
| `tag.username` | `keyword` | Authenticated SSL VPN username | `v.emudhra4` |

---

### 5.3 IPsec VPN Index — `ipsec-normalized`

**Routing:** `OPENSEARCH_IPSEC_URL`  
**Timestamp field:** `@timestamp`

| Field Path | Mapping Type | Description | Example |
|-----------|-------------|-------------|---------|
| `measurement_name` | `keyword` | Always `ipsec_normalized` | `ipsec_normalized` |
| `ipsec_normalized.bytes_in` | `long` | Bytes received through the IPsec tunnel | `3150717` |
| `ipsec_normalized.bytes_out` | `long` | Bytes transmitted through the IPsec tunnel | `21871145` |
| `ipsec_normalized.tunnel_lifetime` | `long` | Duration the tunnel has been active (seconds) | `41225` |
| `tag.device` | `keyword` | FortiGate device identifier | `FGT-DRC` |
| `tag.username` | `keyword` | IPsec tunnel or user identifier | `ervinda.pratama` |
| `tag.remote_gw_ip` | `ip` | Remote gateway public IP address | `103.184.181.50` |
| `tag.assigned_ip` | `ip` | Inner IP assigned to the IPsec peer | `10.90.148.90` |

---

### 5.4 SNMP Interface Stats — `telegraf-index*` *(New — v1.1.0 Placeholder)*

> **⚠ STATUS: PLACEHOLDER — Implementation of FR-17 is BLOCKED until this section is finalized.**

**Routing:** `OPENSEARCH_TELEGRAF_URL`  
**Timestamp field:** `@timestamp`  
**Discriminator:** `measurement_name: interface_stats` *(provisional — see Rule Q-06 compliance note below)*

> **Critical Note — Rule Q-06 Compliance:** The exact `measurement_name` value used by the Telegraf plugin for FortiGate interface statistics **must** be determined from the live pipeline configuration and declared in `.env` as `TELEGRAF_IFSTATS_MEASUREMENT` before any production query targeting this data is written. The string `"interface_stats"` is a provisional placeholder only. Writing any query that uses a wildcard or prefix match on `measurement_name` is strictly prohibited regardless of validation status.

> **Pipeline Validation Checklist (Blocking):** Engineering must confirm and document: (a) the exact `measurement_name` value; (b) the exact nested field path structure under `interface_stats.*`; (c) whether utilization is stored as pre-computed percentage or as raw byte counters requiring computation; (d) the operational status field encoding (keyword string `up`/`down` vs. integer). Section 5.4 must be updated with confirmed values before FR-17 implementation begins.

| Field Path *(Provisional)* | Mapping Type | Description | Example |
|---------------------------|-------------|-------------|---------|
| `measurement_name` | `keyword` | Measurement discriminator | `interface_stats` *(provisional)* |
| `interface_stats.ifname` | `keyword` | Interface name | `port1`, `wan1` |
| `interface_stats.oper_status` | `keyword` | Operational link status | `up`, `down` |
| `interface_stats.in_util_pct` | `float` | Inbound utilization percentage (0–100) | `34.7` |
| `interface_stats.out_util_pct` | `float` | Outbound utilization percentage (0–100) | `12.1` |
| `interface_stats.in_errors` | `long` | Inbound error counter | `0` |
| `interface_stats.out_errors` | `long` | Outbound error counter | `0` |
| `interface_stats.in_discards` | `long` | Inbound discard counter | `0` |
| `interface_stats.out_discards` | `long` | Outbound discard counter | `0` |
| `tag.device` | `keyword` | FortiGate device identifier | `FGT-DC` |

---

---

## Section 6 — Functional Requirements (Revised)
> **This section replaces FR-01 and FR-02 in their entirety, and appends FR-17 as a new requirement.**
> **FR-03 through FR-16 are unchanged from v1.0.0 and must be retained as-is.**

---

### FR-01: Overview Dashboard (Summary)
> *Updated in v1.1.0: Panels P01-J and P01-K added.*

**Priority:** P0 — Must Have

**Description:** The landing page presented immediately after login. Provides a high-level operational snapshot across all data sources. All panels auto-refresh on a configurable interval (default: 60 seconds). Clicking any panel navigates to the corresponding dedicated view.

**Panels Required:**

| Panel ID | Title | Data Source | Query Type | Chart Type |
|----------|-------|------------|-----------|-----------|
| P01-A | Active SSL VPN Users | `telegraf-index*` (SSLVPN) | `cardinality` agg on `tag.username` | KPI Card |
| P01-B | Active IPsec VPN Users | `ipsec-normalized` | `cardinality` agg on `tag.username` | KPI Card |
| P01-C | FortiGate CPU (per device) | `telegraf-index*` (ha_member) | `top_hits` (size:1, sort `@timestamp` desc) per `tag.device` → `ha_member.cpu_usage` | Gauge Cards |
| P01-D | FortiGate Memory (per device) | `telegraf-index*` (ha_member) | `top_hits` (size:1) per `tag.device` → `ha_member.mem_usage` | Gauge Cards |
| P01-E | Active Sessions (per device) | `telegraf-index*` (ha_member) | `top_hits` (size:1) per `tag.device` → `ha_member.session_count` | KPI Card with sparkline |
| P01-F | HA Sync Status | `telegraf-index*` (ha_member) | `top_hits` (size:1) per `tag.device` → `ha_member.sync_status` | Status Badge (In Sync / Out of Sync) |
| P01-G | Top 5 Traffic Applications | `fortigate-appid-flow-*` | `terms` agg (size:5) on `flow.application.name`, sub-agg `sum` of client+server bytes | Horizontal Bar Chart |
| P01-H | SD-WAN Link Status | `telegraf-index*` (SD-WAN SLA) | `top_hits` (size:1) per configured site → `status_link1`, `status_link2` | Status Table |
| P01-I | Total Throughput (selected window) | `fortigate-appid-flow-*` | `sum` of `flow.client.bytes` + `flow.server.bytes` within `@timestamp` range | KPI Card |
| P01-J | Active VPN Users Table *(New — v1.1.0)* | `telegraf-index*` (SSLVPN) + `ipsec-normalized` | See specification below | Compact Data Table |
| P01-K | Top Destination Organizations *(New — v1.1.0)* | `fortigate-appid-flow-*` | `terms` agg (size:5) on `flow.destination_organization`, sub-agg `sum` of client+server bytes | Horizontal Bar Chart |

---

**Panel Specification — P01-J (Active VPN Users Table):**

Displays a unified, combined list of currently active VPN users from both SSL VPN and IPsec VPN tunnels, alongside each user's remote IP address.

*Query Design:*
Two parallel `async` queries are issued via `multi_search` in a single OpenSearch request against their respective endpoints, then merged at the Python service layer before serialization:

- **SSL VPN query** (`OPENSEARCH_TELEGRAF_URL`): For each measurement_name in `TELEGRAF_SSLVPN_SITES`, execute a `terms` aggregation on `tag.username` with `size: 20`, containing a `top_hits` sub-aggregation (size:1, sort: `@timestamp` desc) to retrieve `remote_ip` and `tag.device` for each user. Each query must include an exact `term` filter on `measurement_name` (Rule Q-06) and a `@timestamp` `range` filter with explicit `gte` and `lte` (Rule Q-01).
- **IPsec VPN query** (`OPENSEARCH_IPSEC_URL`): `terms` aggregation on `tag.username` with `size: 20`, containing a `top_hits` sub-aggregation (size:1, sort: `@timestamp` desc) to retrieve `tag.remote_gw_ip` and `tag.device`. Must include `@timestamp` `range` filter with explicit `gte` and `lte` (Rule Q-01).

*Table Columns:* Username | VPN Type (SSL / IPsec) | Remote IP | Device | Last Seen

*Display Constraint:* The panel in the Overview context shows a maximum of 10 rows without vertical scrolling. A "View All" link navigates to the VPN Sessions dedicated page. The `size: 20` aggregation ensures up to 20 unique users are available in the response even if only 10 are rendered in the Overview panel.

*Acceptance Criteria:*
- The panel renders correctly when one or both data sources return zero results.
- The `multi_search` payload must never issue more than the total count of configured `TELEGRAF_SSLVPN_SITES` + 1 sub-requests.
- No N+1 query pattern: one `multi_search` call per refresh cycle, not one call per site (Rule Q-07).

---

**Panel Specification — P01-K (Top Destination Organizations):**

Query logic and chart type are identical to P01-G (Top 5 Traffic Applications), substituting `flow.destination_organization` as the aggregation dimension.

- **Query:** `terms` agg (size:5) on `flow.destination_organization`, sub-agg `sum` of `flow.client.bytes` + `flow.server.bytes`, scoped to the global `@timestamp` range (Rule Q-01). Explicit `size: 5` (Rule Q-02).
- **Chart Type:** Horizontal Bar Chart (Tremor `BarChart`, `layout="horizontal"`).
- **Field Dependency:** Requires `flow.destination_organization` to be validated and populated in the ingest pipeline (Section 5.1). If the field is absent or returns empty buckets, the panel must render a non-blocking "Data unavailable — field not indexed" empty state. It must **not** fail silently or render a blank canvas.

---

**Acceptance Criteria (FR-01 v1.1.0):**
- All 11 panels (P01-A through P01-K) render within 3 seconds on a LAN-connected client.
- Auto-refresh does not issue the next request until the previous one completes or times out (5-second timeout per panel).
- Panel P01-A and P01-B are scoped to the selected global timeframe.
- An individual panel query failure renders a non-blocking "Data unavailable" state without affecting adjacent panels.
- P01-J renders correct data for both VPN types independently; a complete absence of IPsec data does not suppress the SSL VPN rows, and vice versa.
- P01-K renders a graceful empty state if `flow.destination_organization` is not available; it does not error.

---

### FR-02: Traffic Flow View
> *Fully restructured in v1.1.0 from a flat single-page layout to a parent page with 5 dedicated sub-routes.*
> *Components TF-01 through TF-08 from FR-02 v1.0.0 are superseded and retired.*

**Priority:** P0 — Must Have

**Description:** The Traffic Flow section is now a **parent route** hosting five dedicated sub-pages, each targeting a specific analytical workflow against `fortigate-appid-flow-*`. All sub-pages share the global timeframe selector and auto-refresh controls (FR-14). Sub-page navigation is rendered as an expandable sidebar section and/or a secondary horizontal tab bar at the top of the Traffic Flow content area.

**Route Structure:**

```
/dashboard/traffic/             → layout.tsx; default redirect → /summary
├── summary/                    → FR-02.1: Traffic Flow Summary
├── timeseries/                 → FR-02.2: Timeseries Analytics
├── custom-filter/              → FR-02.3: Custom Filter Usage
├── sankey/                     → FR-02.4: Sankey Diagram View
└── conversations/              → FR-02.5: Traffic Details (Conversations)
```

**Retirement Notice:** The following v1.0.0 components are retired from the Traffic Flow section and must be removed from both frontend and backend code. Retained dead code constitutes a violation of the Anti-Bloatware Policy (Section 8.1):

| Retired Component | v1.0.0 ID | Reason |
|------------------|-----------|--------|
| Application Category Donut Chart | TF-02 | Analytical scope consolidated into sub-page focused views |
| Top Client IPs standalone panel | TF-05 | Subsumed into TFS-01 (Summary, "By Client" toggle) |
| Top Server IPs standalone panel | TF-06 | Retired; not assigned to any sub-page in v1.1.0 |
| Protocol Distribution chart | TF-07 | Retired; available via Custom Filter (FR-02.3) ad-hoc queries |
| Egress Interface Breakdown chart | TF-08 | Retired; represented in Sankey (FR-02.4) egress node |

---

#### FR-02.1: Sub-page 2.1 — Traffic Flow Summary

**Route:** `/dashboard/traffic/summary`

**Description:** The default landing sub-page for the Traffic Flow section. Provides a high-level, bandwidth-oriented summary with no complex filtering. Designed for rapid operational overview.

**Components Required:**

| Component ID | Component | Fields Used | Query Type |
|-------------|-----------|------------|-----------|
| TFS-01 | Top N Applications / Clients Data Table | `flow.application.name` (default) / `flow.client.ip.addr` (toggle) | `terms` agg, size: configurable 5/10/20, with `sum` sub-agg on byte fields |
| TFS-02 | Throughput Over Time Chart | `@timestamp`, `flow.client.bytes`, `flow.server.bytes` | `date_histogram` (adaptive interval) + `sum` sub-agg on byte fields |

**TFS-01 Specification:**
- A dimension toggle ("By Application" / "By Client") switches the `terms` agg target field between `flow.application.name` and `flow.client.ip.addr`. The toggle triggers a fresh query; no client-side re-slicing of existing data.
- The Top N selector (5 / 10 / 20) is rendered as a dropdown. The selected value is passed as the explicit `size` parameter (Rule Q-02).
- Table columns: Rank, Name / IP, Total Bytes (auto-scaled: KB/MB/GB), % of Total Traffic.
- "% of Total Traffic" is derived from the same query response (`sum_other_doc_count` + sum of bucket values) and computed in the backend Python service layer before API serialization.

**TFS-02 Specification:**
- Chart type: Tremor `AreaChart` (single series — total bytes over time).
- Adaptive `fixed_interval` — backend must select the interval from the following schedule based on the active global timeframe duration:

| Timeframe Duration | `fixed_interval` | Interval in Seconds (for Mbps calc) |
|-------------------|-----------------|--------------------------------------|
| ≤ 2 hours | `1m` | 60 |
| ≤ 12 hours | `5m` | 300 |
| ≤ 24 hours | `15m` | 900 |
| ≤ 7 days | `1h` | 3600 |
| > 7 days | `6h` | 21600 |

- Y-axis unit: auto-scaled bytes (KB / MB / GB) with formatted tooltip values.

**Query Compliance:**
- Rule Q-01: All queries must include `range` on `@timestamp` with explicit `gte` and `lte`.
- Rule Q-02: `terms` agg `size` must be set explicitly to the UI-selected value (5, 10, or 20).
- Both TFS-01 and TFS-02 are aggregation-only queries; `_source` filtering does not apply.

---

#### FR-02.2: Sub-page 2.2 — Timeseries Analytics

**Route:** `/dashboard/traffic/timeseries`

**Description:** A TSVB-equivalent Stacked Area Chart displaying per-application throughput in Mbps over time. Designed for traffic pattern analysis and anomaly detection at the application level. Supports interactive brushing/zooming for temporal range refinement without modifying the global timeframe selector.

**Components Required:**

| Component ID | Component | Fields Used | Query Type |
|-------------|-----------|------------|-----------|
| TSVA-01 | Per-Application Stacked Area Chart (Mbps) | `@timestamp`, `flow.application.name`, `flow.client.bytes`, `flow.server.bytes` | `date_histogram` (adaptive interval) with `terms` sub-agg (size:10) + `sum` sub-agg on byte fields per application per bucket |
| TSVA-02 | Application Series Selector | `flow.application.name` | UI filter — limits which application series are rendered; no dedicated query |

**TSVA-01 Specification:**
- **Chart implementation:** Tremor `AreaChart` with `stack={true}` to produce a stacked area visualization.
- **Mbps Computation Formula (Mandatory — Backend Python Layer):**
  For each `date_histogram` bucket of duration `T` seconds (resolved from the `fixed_interval` used in the query), and for each application `terms` sub-bucket within it:

  ```
  throughput_mbps = (sum(flow.client.bytes + flow.server.bytes) × 8) / T
  ```

  - `T` is the integer interval in seconds as defined in the TFS-02 adaptive schedule (FR-02.1).
  - This computation is performed by the **backend Python service** after retrieving the aggregation response from OpenSearch. It is a unit-conversion of an aggregated value — it does **not** violate Rule Q-05, which prohibits fetching raw documents for aggregation-equivalent computations.
  - The backend must serialize the response with pre-computed Mbps values. Raw byte sums must not be returned to the frontend for Mbps computation.

- **Series Limit:** The `terms` sub-aggregation within `date_histogram` must use explicit `size: 10` (Rule Q-02). Applications beyond the top 10 by total bytes in the window are consolidated into an "Other" series, computed by the backend as `(total_bytes_in_bucket - sum_of_top10_bytes) × 8 / T`.
- **Adaptive Interval:** Same schedule as TFS-02 applies.

**Brushing / Zooming Interaction:**
- The chart canvas must support mouse-drag selection of a horizontal time range (brush interaction). Full interaction specification is defined in Section 9.4 (Chart Standards).
- When a brush selection is active, TSVA-01 re-queries using the brushed `[start_ts, end_ts]` as an override `@timestamp` `range` filter. Both bounds are explicit (Rule Q-01 compliance maintained).
- The brush override is **local to Sub-page 2.2** and does not propagate to the global timeframe selector or to any other sub-page.
- A "Reset Zoom" button must appear in the chart panel header when a brush is active; clicking it restores the global timeframe as the active query filter.
- The active brush range must be serialized to URL query parameters (`brush_start`, `brush_end` as ISO 8601 strings) for deep-linking and browser back/forward navigation.

**Query Compliance:**
- Rule Q-01: `@timestamp` range with explicit `gte` and `lte` required on all queries, whether sourced from the global timeframe or the active brush selection.
- Rule Q-02: `terms` sub-agg within `date_histogram` must specify `size: 10` explicitly.
- The backend module `app/opensearch/appid/timeseries.py` is responsible for the Mbps conversion; the route handler `app/api/traffic/timeseries.py` must not perform this computation.

---

#### FR-02.3: Sub-page 2.3 — Custom Filter Usage

**Route:** `/dashboard/traffic/custom-filter`

**Description:** A manual filter interface for ad-hoc investigation queries. Users construct filter conditions by entering values into a structured form; the backend translates the active form state into scoped OpenSearch DSL filter clauses appended to the global timeframe query.

**Components Required:**

| Component ID | Component | Fields Used | Query Type |
|-------------|-----------|------------|-----------|
| TCF-01 | Custom Filter Form | `flow.client.ip.addr`, `flow.server.ip.addr`, `flow.application.name`, `flow.dst.l4.port.id`, `l4.proto.name` | UI state — drives query construction on submit |
| TCF-02 | Filtered Results Summary Table | Active filter fields + byte fields | `terms` agg (configurable dimension, size: 20) + `sum` sub-agg |
| TCF-03 | Filtered Throughput Timeline | `@timestamp`, `flow.client.bytes`, `flow.server.bytes` | `date_histogram` (adaptive interval) + `sum` sub-agg with all active filter clauses applied |

**TCF-01 Filter Form Specification:**

| Field Label | Input Type | OpenSearch Field | Filter DSL Clause |
|-------------|-----------|-----------------|-------------------|
| Client IP | Text (IP format validation via Zod) | `flow.client.ip.addr` | `term` |
| Server IP | Text (IP format validation via Zod) | `flow.server.ip.addr` | `term` |
| Application Name | Text (partial match) | `flow.application.name` | `wildcard` (user input + `*` suffix) |
| Destination Port | Number input, range 1–65535 (Zod validation) | `flow.dst.l4.port.id` | `term` |
| Protocol | Dropdown: TCP / UDP / All | `l4.proto.name` | `term` (omitted when "All" selected) |

- **Implementation:** React Hook Form + Zod schema validation. No Formik.
- All fields are optional. An empty form is valid; results reflect the global timeframe only.
- **Minimum Query Gate:** The backend must enforce: at least one non-empty filter field must be present, OR the active timeframe must be ≤ 6 hours in duration. If neither condition is met, the API returns HTTP 400 with `VALIDATION_ERROR` code, preventing accidental unbounded-scan submissions.
- **Wildcard Safety:** The Application Name `wildcard` filter must enforce a minimum prefix length of **3 characters** at the backend validation layer before the wildcard query is issued. Prefixes shorter than 3 characters are rejected with `VALIDATION_ERROR`.
- Active filter state must be serialized to URL query parameters for deep-linking.

**Query Compliance:**
- Rule Q-01: The global timeframe `range` filter on `@timestamp` is always applied. Custom filter clauses are appended as additional `filter` terms — they never replace the time bounds.
- Rule Q-02: `terms` agg in TCF-02 must specify `size: 20` explicitly.
- Rule Q-03: `_source` filtering must be applied if any document-fetch path is introduced (currently aggregation-only).

---

#### FR-02.4: Sub-page 2.4 — Sankey Diagram View

**Route:** `/dashboard/traffic/sankey`

**Description:** A dedicated full-viewport Sankey Diagram page visualizing the traffic flow from ingress zone, through top applications and egress interface, to destination country. This sub-page supersedes and extends the TF-03 Sankey component from FR-02 v1.0.0.

**Components Required:**

| Component ID | Component | Fields Used | Query Type |
|-------------|-----------|------------|-----------|
| TSK-01 | 4-Node Sankey Diagram | `flow.in.netif.name`, `flow.application.name`, `flow.out.netif.descr`, `flow.as_country` | `composite` agg (4 sources, size:500) + `sum` sub-agg on byte fields |

**Sankey Flow Definition:**

```
Zone (Ingress)         →    Top Applications     →   Egress Interface      →   AS Country
[flow.in.netif.name]   [flow.application.name]  [flow.out.netif.descr]   [flow.as_country]
```

**TSK-01 Specification:**
- **Implementation:** `d3-sankey` isolated import within a custom React wrapper. No full `d3` bundle import (consistent with approved dependency policy).
- **Query:** `composite` aggregation with 4 source fields:
  ```json
  "composite": {
    "size": 500,
    "sources": [
      { "zone":    { "terms": { "field": "flow.in.netif.name" } } },
      { "app":     { "terms": { "field": "flow.application.name" } } },
      { "egress":  { "terms": { "field": "flow.out.netif.descr" } } },
      { "country": { "terms": { "field": "flow.as_country" } } }
    ]
  }
  ```
  With a `sum` sub-aggregation on `flow.client.bytes + flow.server.bytes` per composite bucket.
- **Composite Pagination:** If the `composite` response includes an `after_key`, the backend must issue a follow-up paginated request using that cursor. Maximum total combinations processed per render cycle: **1,000** (i.e., at most 2 composite requests of `size: 500`). This cap prevents runaway pagination on high-cardinality data.
- **Data Processing:** The backend `app/opensearch/appid/sankey.py` module constructs the Sankey node-link structure from the composite response and returns it in the standard API response envelope. Raw composite aggregation responses must not be forwarded to the frontend.
- **`flow.as_country` Degradation Path:** If `flow.as_country` returns empty values or is absent, the Sankey must fall back gracefully to a **3-node layout** (Zone → App → Egress Interface) and render a non-blocking notice: "Country data unavailable — contact pipeline engineering." This fallback must not require a separate backend code path; the data-processing module must detect the absence and degrade automatically.
- **Rule Q-02 Compliance:** `composite` size is explicitly `500`. The maximum total processed is capped at 1,000 per the pagination rule above.

**Query Compliance:**
- Rule Q-01: `@timestamp` range with explicit `gte` and `lte` required.
- Rule Q-02: `composite` size set to `500` explicitly.
- Rule Q-04: Pagination via `composite after_key` cursor — no `scroll` API.

---

#### FR-02.5: Sub-page 2.5 — Traffic Details (Conversations)

**Route:** `/dashboard/traffic/conversations`

**Description:** Provides conversation-level granularity for traffic investigation. Renders a timeseries visualization of conversation activity volume and a paginated data table of top conversations in the selected time range.

**Components Required:**

| Component ID | Component | Fields Used | Query Type |
|-------------|-----------|------------|-----------|
| TCD-01 | Conversation Volume Timeline | `@timestamp`, `conversation_id` | `date_histogram` (adaptive interval) + `cardinality` sub-agg on `conversation_id` |
| TCD-02 | Conversations Data Table | `conversation_id`, `flow.client.ip.addr`, `flow.server.ip.addr`, `flow.application.name`, `flow.dst.l4.port.id`, `l4.proto.name`, `flow.client.bytes`, `flow.server.bytes`, `flow.packets` | `terms` agg on `conversation_id` (size:50) + `sum`, `value_count`, and `top_hits` sub-aggs |

**TCD-02 Column Specification:**

| Column Header | Source / Derivation | OpenSearch Mechanism |
|--------------|---------------------|----------------------|
| Conversation ID | `conversation_id` | `terms` agg bucket key |
| Client | `flow.application.name` | `top_hits` (size:1, sort `@timestamp` desc) sub-agg per conversation |
| Client IP | `flow.client.ip.addr` | `top_hits` (size:1) sub-agg per conversation |
| Server | `flow.server.ip.addr` (display as-is) | `top_hits` (size:1) sub-agg per conversation |
| Server IP | `flow.server.ip.addr` | `top_hits` (size:1) sub-agg per conversation |
| Service | `l4.proto.name` + `flow.dst.l4.port.id` formatted as `TCP/443` | `top_hits` (size:1) sub-agg per conversation |
| Bytes | `sum(flow.client.bytes + flow.server.bytes)` | `sum` sub-agg |
| Packets | `sum(flow.packets)` | `sum` sub-agg |
| Sessions | Count of flow documents in conversation bucket | `value_count` sub-agg on `@timestamp` |

- **`sessions` Column Implementation:** Computed as `value_count` on `@timestamp` per `conversation_id` bucket. This is not a stored field; no pipeline validation is required for this column.
- **`conversation_id` Degradation Path:** If `conversation_id` is absent or unmapped in the index, the sub-page must render a non-blocking notice: "Conversation ID field unavailable — contact pipeline engineering." TCD-01 must fall back to displaying total document count per time bucket. TCD-02 must be hidden entirely. Neither component may return an unhandled query error to the UI.
- **Pagination:** `terms` agg on `conversation_id` uses `size: 50` (Rule Q-02). Server-side pagination for deeper pages uses `search_after` with a composite sort key (`@timestamp` + `_id`).

**Query Compliance:**
- Rule Q-01: `@timestamp` range with explicit `gte` and `lte` on all queries.
- Rule Q-02: `terms` agg `size: 50` explicit; `top_hits` sub-agg `size: 1` explicit.
- Rule Q-05: All Bytes and Packets totals computed by OpenSearch `sum` aggregation. No application-layer summing of fetched documents.
- Rule Q-08: `from + size ≤ 10,000` enforced; `search_after` used for deeper pagination pages.

---

> **FR-03 through FR-16: Unchanged from v1.0.0. Retain as-is.**

---

### FR-17: SNMP Interface Stats View *(New — v1.1.0)*

**Priority:** P1 — Should Have  
**Status:** ⚠️ **IMPLEMENTATION BLOCKED** — This FR may not be developed until Section 5.4 is updated with confirmed pipeline field values. Frontend scaffolding (route, page shell, empty state) may be built speculatively, but no production OpenSearch query targeting `telegraf-index*` for interface data may be written, committed, or merged until the pipeline validation checklist in Section 5.4 is completed.

**Route:** `/dashboard/snmp`

**Description:** A dedicated monitoring view for FortiGate interface-level SNMP statistics collected via Telegraf. Provides link status visibility, in/out utilization percentage timelines, and error/discard counter charts per interface per device.

**New Environment Variable (Required Before Query Implementation):**

```dotenv
# Exact measurement_name value for FortiGate interface statistics in telegraf-index*
# MUST be confirmed from live pipeline — do not use this placeholder value in production queries
TELEGRAF_IFSTATS_MEASUREMENT=interface_stats
```

The backend `app/core/config.py` Pydantic Settings model must expose this variable. The `app/opensearch/snmp_interface.py` query module must read its value at startup and use it as the exact `term` filter value for all `measurement_name` filters (Rule Q-06). No hardcoded string `"interface_stats"` may appear in query code.

**Components Required:**

| Component ID | Component | Fields Used *(Provisional — Section 5.4)* | Query Type |
|-------------|-----------|------------------------------------------|-----------|
| SNMP-01 | Device + Interface Selector | `tag.device`, `interface_stats.ifname` | UI filter; drives all component queries |
| SNMP-02 | Utilization Line Chart | `@timestamp`, `interface_stats.in_util_pct`, `interface_stats.out_util_pct`, `tag.device`, `interface_stats.ifname` | `date_histogram` (adaptive interval) + `avg` sub-agg per interface |
| SNMP-03 | Link Status Badges | `interface_stats.oper_status`, `interface_stats.ifname`, `tag.device` | `top_hits` (size:1, sort `@timestamp` desc) per interface within `terms` agg |
| SNMP-04 | Errors / Discards Timeline | `@timestamp`, `interface_stats.in_errors`, `interface_stats.out_errors`, `interface_stats.in_discards`, `interface_stats.out_discards` | `date_histogram` (adaptive interval) + `sum` sub-agg per counter |
| SNMP-05 | Interface Detail Table | All `interface_stats.*` fields, `tag.device` | `terms` agg on `interface_stats.ifname` (size:50) + `top_hits` (size:1) for current values |

**Component Specifications:**

- **SNMP-02 (Utilization Line Chart):** Tremor `LineChart` with two series per selected interface — "In Utilization %" and "Out Utilization %". Y-axis range: 0–100%. Adaptive `fixed_interval` consistent with the schedule defined in FR-02.1 (TFS-02). Values are rendered as percentages; backend must not return raw byte counters if the field is confirmed as a pre-computed percentage.

- **SNMP-03 (Link Status Badges):** Each monitored interface is represented by a shadcn/ui `Badge` component. Status-to-color mapping using the semantic CSS token system:
  - `up` → `--success` (green) with label "UP"
  - `down` / any non-`up` value → `--destructive` (red) with label "DOWN"
  - Status must be conveyed by both color **and** text label. Color-only indicators are prohibited (Section 9.7 — Accessibility).

- **SNMP-04 (Errors/Discards Timeline):** Tremor `LineChart` with four series: In Errors, Out Errors, In Discards, Out Discards. Rendered as absolute cumulative counters. Delta computation (rate of change per interval) is out of scope for v1.1.0.

- **SNMP-05 (Interface Detail Table):** One row per interface. Columns: Interface Name | Status | In Util % | Out Util % | In Errors | Out Errors | In Discards | Out Discards | Last Updated. "Current" values retrieved via `top_hits` (size:1, sort `@timestamp` desc) within a `terms` agg on `interface_stats.ifname`.

**Query Requirements:**
- Every query against `telegraf-index*` must include an exact `term` filter on `measurement_name` using the value from `TELEGRAF_IFSTATS_MEASUREMENT` env variable (Rule Q-06 — non-negotiable).
- All queries must include `range` on `@timestamp` with explicit `gte` and `lte` (Rule Q-01 — non-negotiable).
- "Current status" queries (SNMP-03, SNMP-05 current value column) must use `top_hits` (size:1, sort `@timestamp` desc) within a `terms` aggregation. No unbounded `match_all` fetches.
- Multi-interface, multi-device queries must issue a single aggregation request using nested `terms` aggs on `tag.device` and `interface_stats.ifname` — not one query per interface (Rule Q-07).
- All `terms` aggregations must specify explicit `size`: `size: 50` for SNMP-05, `size: 1` for `top_hits` sub-aggs (Rule Q-02).

**Acceptance Criteria:**
- FR-17 must not enter code review until Section 5.4 is updated with confirmed, live-validated field values.
- Utilization timeline values match values independently verifiable in `telegraf-index*` via direct OpenSearch query.
- Link Status badges correctly reflect the most recent `oper_status` value per interface.
- A device with no data in the selected time range renders an explicit "No data" empty state per panel.
- All `measurement_name` filter values are sourced exclusively from `TELEGRAF_IFSTATS_MEASUREMENT` — no hardcoded strings in query code.

---

---

## Section 9 — UI/UX Guidelines (Revised)
> **Section 9.2 and 9.4 updated in v1.1.0. Sections 9.1, 9.3, 9.5, 9.6, 9.7 are unchanged.**

### 9.1 Design System Reference

The dashboard strictly follows the aesthetic, component conventions, and interaction patterns of the **`next-shadcn-admin-dashboard`** reference template. Custom components are permitted only when no shadcn/ui primitive satisfies the functional requirement. All custom components must extend shadcn/ui primitives and use the defined CSS variable token system.

---

### 9.2 Page Layout Structure *(Updated — v1.1.0)*
> *Traffic Flow expanded to a collapsible sub-menu; SNMP Interfaces added as top-level item.*

```
┌────────────────────────────────────────────────────────────────────┐
│  TOP NAV: [Logo]  [Nav Links]  [Search]  [Bell+Badge]  [Avatar]   │
├────────────────────────────────────────────────────────────────────┤
│ SIDEBAR             │  MAIN CONTENT AREA                           │
│ (collapsible)       │                                              │
│                     │  ┌────────────────────────────────────────┐ │
│ ▸ Overview          │  │  Page Title  |  Timeframe Picker  |    │ │
│ ▼ Traffic Flow      │  │              |  Auto-refresh Toggle     │ │
│   ├ Summary         │  └────────────────────────────────────────┘ │
│   ├ Timeseries      │                                              │
│   ├ Custom Filter   │  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│   ├ Sankey          │  │ KPI Card │  │ KPI Card │  │ KPI Card │ │
│   └ Conversations   │  └──────────┘  └──────────┘  └──────────┘ │
│ ▸ SD-WAN SLA        │                                              │
│ ▸ Resources         │  ┌────────────────────────────────────────┐ │
│ ▸ VPN Sessions      │  │            Chart Panel                 │ │
│ ▸ Raw Data          │  └────────────────────────────────────────┘ │
│ ▸ SNMP Interfaces   │                                              │
│ ── ─────────────── ─│                                              │
│ ▸ Alerts            │                                              │
│ ▸ Reports           │                                              │
│ ── ─────────────── ─│                                              │
│ ▸ Users             │                                              │
│ ▸ Settings          │                                              │
└─────────────────────────────────────────────────────────────────── ┘
```

**Traffic Flow Sub-Navigation Specification:**
- "Traffic Flow" is an expandable/collapsible sidebar parent node implemented using the shadcn/ui `Collapsible` primitive.
- **Auto-expand behavior:** The Traffic Flow group auto-expands when the active route matches any `/dashboard/traffic/*` path. It collapses to show only the "Traffic Flow" parent label when a non-traffic route is active.
- **Active state:** The active sub-page link is visually distinguished with a filled background (`--muted`) and a left border accent using the `--primary` CSS variable token.
- **Collapsed state:** Sub-page links are hidden. The "Traffic Flow" parent item remains visible with a `▸` expand indicator. Collapsed state does not discard active sub-page query state; navigating away and returning preserves the last active sub-page via URL.
- **Default route:** Navigating to `/dashboard/traffic` must redirect to `/dashboard/traffic/summary` without a perceptible loading flash. Implement via `redirect()` in the Next.js App Router `page.tsx` at the parent route.

**SNMP Interfaces Navigation:**
- "SNMP Interfaces" appears as a **top-level** sidebar item linking to `/dashboard/snmp`.
- Visually grouped above the operational-tools separator line, alongside the other primary monitoring pages (Traffic Flow, SD-WAN SLA, Resources, VPN Sessions, Raw Data).
- The item renders grayed-out with a "Coming Soon" tooltip when `TELEGRAF_IFSTATS_MEASUREMENT` is not set or FR-17 is not deployed. It must never be fully hidden, as its absence from the sidebar would mislead users about planned capabilities.

---

### 9.3 Color Palette & Theming

All colors must use the shadcn/ui CSS variable system. Hardcoded hex values inside component files are prohibited.

| CSS Variable | Light Mode | Dark Mode | Semantic Usage |
|-------------|-----------|----------|----------------|
| `--background` | `#FFFFFF` | `#09090B` | Page background |
| `--foreground` | `#09090B` | `#FAFAFA` | Primary text |
| `--card` | `#FFFFFF` | `#09090B` | Panel and card backgrounds |
| `--primary` | `#18181B` | `#FAFAFA` | Primary interactive elements |
| `--muted` | `#F4F4F5` | `#27272A` | Subdued backgrounds, skeleton loaders |
| `--destructive` | `#EF4444` | `#EF4444` | CRITICAL alerts, errors |
| `--warning` *(custom token)* | `#F59E0B` | `#F59E0B` | WARNING alerts, caution states |
| `--success` *(custom token)* | `#10B981` | `#10B981` | OK status, resolved alerts |
| `--border` | `#E4E4E7` | `#27272A` | Component borders and dividers |

---

### 9.4 Chart Standards *(Updated — v1.1.0)*
> *Brushing/Zooming interaction standard added for Sub-page 2.2.*

All analytical charts must use Tremor components except the Sankey diagram (d3-sankey).

| Chart Type | Tremor Component | Usage |
|-----------|-----------------|-------|
| Time-series | `AreaChart` or `LineChart` | CPU, latency, jitter, throughput timelines |
| Stacked Time-series (Mbps) | `AreaChart` (`stack={true}`) | FR-02.2 per-application throughput |
| Ranking / Top-N | `BarChart` (horizontal) | Top applications, top clients, top organizations |
| Proportional | `DonutChart` | Category distribution |
| KPI | `Metric` + `Flex` | Overview cards with trend indicators |
| Sankey | d3-sankey (custom React wrapper) | Traffic flow: zone → app → egress → country |

**Chart Standards:**
- All chart tooltips must display formatted values with appropriate units: `ms` for latency/jitter, `%` for loss/CPU/memory/utilization, `KB/MB/GB` (auto-scaled) for bytes, `Mbps` for throughput series, `K/M` for session and packet counts.
- Charts must display an explicit empty state component ("No data for selected time range") — never a blank canvas.
- Chart legends must be positioned below the chart area, not overlaid on the chart.
- Charts must not block page render; they must use Suspense or SWR loading state with a `Skeleton` placeholder.

---

**Brushing / Zooming Interaction Standard** *(Sub-page 2.2 — Timeseries Analytics, FR-02.2)*

The TSVB-equivalent Stacked Area Chart (TSVA-01) must implement interactive time range selection. The following standards govern all implementations:

| Interaction Property | Specification |
|----------------------|---------------|
| **Trigger** | Mouse `mousedown` + horizontal drag on the chart canvas area |
| **Visual Feedback** | A semi-transparent overlay rectangle rendered during drag; bounds follow cursor in real time |
| **Overlay Color** | `--muted` background token at 40% opacity; boundary lines use `--primary` token |
| **Commit Timing** | The query re-executes on `mouseup` only — not on `mousemove`. No intermediate queries during active drag. |
| **Minimum Brush Width** | Minimum valid brush range = 2 × current `fixed_interval` in seconds (e.g., if interval = `5m`, minimum brush = 10 minutes). Narrower selections are silently discarded; the brush overlay clears and the query is not re-executed. |
| **Reset Control** | A "Reset Zoom" button (shadcn/ui `Button`, variant `outline`, size `sm`) is rendered in the chart panel header and is visible **only** when a brush override is active. Clicking it clears the brush state and restores the global timeframe as the active query filter. |
| **URL State** | Active brush range is serialized to URL params `brush_start` and `brush_end` (ISO 8601 format) for deep-linking and browser back/forward navigation. |
| **Scope Boundary** | Brush selection is **scoped exclusively to Sub-page 2.2**. It must not modify the global timeframe selector state, must not affect any other sub-page, and must not persist across sub-page navigations. |
| **Accessibility** | Two `<input type="datetime-local">` fields (labeled "Zoom Start" and "Zoom End") must be available for keyboard-only users to achieve equivalent time range filtering. These inputs synchronize with the brush state bidirectionally. |

---

### 9.5 Loading States

- shadcn/ui `Skeleton` components replace every panel during initial load and on each refresh cycle.
- Skeleton dimensions must match the expected rendered panel dimensions to prevent cumulative layout shift (CLS).
- Loading state must never collapse the panel's reserved space.

---

### 9.6 Error States

- Each panel handles its own query errors independently.
- A panel in error state renders: an error icon, a concise message ("Query failed"), and a "Retry" button.
- A single panel error must not trigger a page-level error boundary or affect adjacent panels.

---

### 9.7 Accessibility

- All interactive elements must have `aria-label` or `aria-describedby` attributes.
- Status indicators (HA Sync, Link Status, Interface Status) must convey meaning through both color AND a text label. Color-only status indicators are prohibited.
- All modals must trap keyboard focus and support `Escape` to close.
- The contrast ratio for all text must meet WCAG 2.1 AA standard (4.5:1 for normal text, 3:1 for large text).

---

---

## Section 10.3 — Project Directory Structure (Revised)
> **Replaces Section 10.3 in its entirety.**
> Changes: `traffic/` directory expanded into sub-routes; `traffic.py` API module split; `appid.py` query module split; `snmp.py` route and `snmp_interface.py` query module added.

```
network-observability-dashboard/
├── docker-compose.yml
├── .env.example            ← Committed to VCS; no secret values
├── .env                    ← Never committed; listed in .gitignore
├── .gitignore
├── nginx/
│   └── nginx.conf
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.ts
│   └── src/
│       ├── app/                        ← Next.js App Router pages and layouts
│       │   ├── (auth)/
│       │   ├── dashboard/
│       │   │   ├── overview/
│       │   │   ├── traffic/
│       │   │   │   ├── layout.tsx      ← Traffic parent layout; sub-nav rail + sidebar expand logic
│       │   │   │   ├── page.tsx        ← Redirect → /traffic/summary
│       │   │   │   ├── summary/
│       │   │   │   │   └── page.tsx    ← FR-02.1: TFS-01 Top N Table + TFS-02 Throughput Timeline
│       │   │   │   ├── timeseries/
│       │   │   │   │   └── page.tsx    ← FR-02.2: TSVA-01 Stacked Area (Mbps) + brush/zoom
│       │   │   │   ├── custom-filter/
│       │   │   │   │   └── page.tsx    ← FR-02.3: TCF-01 Filter Form + TCF-02/03 Results
│       │   │   │   ├── sankey/
│       │   │   │   │   └── page.tsx    ← FR-02.4: TSK-01 4-Node Sankey Diagram
│       │   │   │   └── conversations/
│       │   │   │       └── page.tsx    ← FR-02.5: TCD-01 Timeline + TCD-02 Conversations Table
│       │   │   ├── sdwan/
│       │   │   ├── resources/
│       │   │   ├── vpn/
│       │   │   ├── raw-data/
│       │   │   ├── snmp/               ← NEW: FR-17 SNMP Interface Stats View
│       │   │   │   └── page.tsx        ← SNMP-01–05 components
│       │   │   ├── alerts/
│       │   │   ├── reports/
│       │   │   ├── users/
│       │   │   └── settings/
│       ├── components/
│       │   ├── ui/                     ← shadcn/ui generated primitives
│       │   ├── charts/                 ← Tremor + d3-sankey wrappers
│       │   │   ├── tsvb-area-chart.tsx ← NEW: Brushable stacked area chart (FR-02.2)
│       │   │   └── sankey-chart.tsx    ← UPDATED: 4-node Sankey wrapper (FR-02.4)
│       │   └── panels/                 ← Dashboard panel compositions
│       ├── lib/
│       │   ├── api.ts                  ← API client (SWR fetchers); traffic sub-routes added
│       │   └── utils.ts                ← Format helpers: bytes, ms, %, Mbps (mbpsFormatter added)
│       └── types/                      ← TypeScript interface definitions
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                        ← DB migration scripts
│   │   └── versions/
│   ├── app/
│   │   ├── main.py                     ← FastAPI app; traffic sub-router and snmp router registered
│   │   ├── api/                        ← Route handlers (one module per FR group)
│   │   │   ├── auth.py
│   │   │   ├── overview.py
│   │   │   ├── traffic/                ← SPLIT from monolithic traffic.py (v1.0.0)
│   │   │   │   ├── __init__.py         ← Sub-router aggregation; mounted at /api/v1/traffic
│   │   │   │   ├── summary.py          ← FR-02.1: /api/v1/traffic/summary endpoints
│   │   │   │   ├── timeseries.py       ← FR-02.2: /api/v1/traffic/timeseries (Mbps transform)
│   │   │   │   ├── custom_filter.py    ← FR-02.3: /api/v1/traffic/custom-filter endpoints
│   │   │   │   ├── sankey.py           ← FR-02.4: /api/v1/traffic/sankey (composite agg)
│   │   │   │   └── conversations.py    ← FR-02.5: /api/v1/traffic/conversations endpoints
│   │   │   ├── sdwan.py
│   │   │   ├── resources.py
│   │   │   ├── vpn.py
│   │   │   ├── raw_data.py
│   │   │   ├── snmp.py                 ← NEW: FR-17 /api/v1/snmp/interfaces endpoints
│   │   │   ├── alerts.py
│   │   │   ├── reports.py
│   │   │   ├── users.py
│   │   │   ├── logs.py
│   │   │   └── notifications.py
│   │   ├── core/
│   │   │   ├── config.py               ← Pydantic Settings; TELEGRAF_IFSTATS_MEASUREMENT added
│   │   │   ├── security.py             ← JWT, bcrypt
│   │   │   └── logging.py              ← Structured logger setup
│   │   ├── db/
│   │   │   ├── models.py               ← SQLAlchemy ORM models
│   │   │   └── session.py              ← Async DB session factory
│   │   ├── opensearch/                 ← Query builder modules (one per data domain)
│   │   │   ├── client.py               ← AsyncOpenSearch client pool per endpoint
│   │   │   ├── appid/                  ← SPLIT from monolithic appid.py (v1.0.0)
│   │   │   │   ├── __init__.py
│   │   │   │   ├── summary.py          ← TFS-01, TFS-02 query builders (Rule Q-01/Q-02)
│   │   │   │   ├── timeseries.py       ← TSVA-01 query builder + Mbps transform function
│   │   │   │   ├── custom_filter.py    ← TCF-02, TCF-03 query builders + wildcard safety guard
│   │   │   │   ├── sankey.py           ← TSK-01 composite agg + pagination cursor logic
│   │   │   │   └── conversations.py    ← TCD-01, TCD-02 query builders (top_hits + value_count)
│   │   │   ├── sdwan.py                ← telegraf SD-WAN SLA queries (unchanged)
│   │   │   ├── ha.py                   ← telegraf ha_member queries (unchanged)
│   │   │   ├── sslvpn.py               ← telegraf SSLVPN queries (unchanged)
│   │   │   ├── ipsec.py                ← ipsec-normalized queries (unchanged)
│   │   │   └── snmp_interface.py       ← NEW: telegraf interface_stats queries (FR-17; BLOCKED)
│   │   ├── services/
│   │   │   ├── alert_engine.py         ← APScheduler alert polling
│   │   │   ├── report_generator.py
│   │   │   ├── notifiers/
│   │   │   │   ├── telegram.py
│   │   │   │   ├── email.py
│   │   │   │   ├── whatsapp.py
│   │   │   │   └── discord.py
│   │   │   └── chart_renderer.py       ← Matplotlib server-side charts
│   │   └── schemas/                    ← Pydantic request/response models
│   └── scripts/
│       └── seed_superadmin.py
├── reports/
│   └── templates/                      ← Jinja2 HTML templates for reports
├── logs/                               ← Host-mounted log volume
│   ├── access.log
│   └── error.log
└── tests/
    ├── unit/
    └── integration/
```

---

---

## Appendix B — Revision History (Revised)
> **Append the row below to the existing Revision History table.**

| Version | Date | Author | Summary of Changes |
|---------|------|--------|--------------------|
| 1.0.0 | June 2026 | Principal Architect | Initial release — full requirements, data dictionary, architecture, anti-bloatware policy |
| 1.1.0 | June 2026 | Principal Architect | **FR-02** restructured from flat view to 5 sub-routes (Summary, Timeseries Analytics with Mbps + Brush/Zoom, Custom Filter, Sankey 4-node, Conversations); components TF-02/05/06/07/08 retired. **FR-01** extended: Panel P01-J (Active VPN Users Table — SSL + IPsec combined) and P01-K (Top Destination Organizations bar chart) added. **FR-17** (SNMP Interface Stats View) added as P1 requirement, implementation blocked pending pipeline validation. **Section 5.1** extended with 4 provisional fields: `flow.as_country`, `flow.destination_organization`, `conversation_id` (all marked REQUIRES PIPELINE VALIDATION), and `sessions` aggregation-derived metric. **Section 5.4** added as placeholder data dictionary for `interface_stats` measurement. **Section 9.2** updated: Traffic Flow sidebar expanded to collapsible sub-menu; SNMP Interfaces added as top-level nav item. **Section 9.4** extended with Brushing/Zooming interaction standard (full specification). **Section 10.3** updated: `traffic/` directory expanded to 5 sub-routes; `appid.py` and `traffic.py` backend modules split by sub-domain; `snmp.py` route and `snmp_interface.py` query module added. |

---

*Network Observability Dashboard PRD v1.1.0 — Internal Confidential*
*Updated Sections Document — For integration into main PRD only*
