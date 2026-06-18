# PRD Comparison Report: PRD_Doc1.md (v1.0.0) vs PRD_by_Qwen.md (v1.1.0)

**Generated:** 2026-06-10  
**Project:** Network Observability Dashboard (NOD)

---

## Document Metadata

| Attribute | PRD_Doc1.md | PRD_by_Qwen.md |
|-----------|-------------|----------------|
| **Version** | 1.0.0 | 1.1.0 |
| **Status** | Draft — Pending Engineering Review | Draft — Pending Engineering Review |
| **Document Owner** | *(not specified)* | Principal Architect |
| **Total Lines** | 1,520 | 1,693 (+173) |
| **File Size** | ~91 KB | ~68 KB |

---

## 1. Executive Summary (Section 1)

Minor wording refinements only. v1.1.0 adds "designed specifically for", changes "near-real time" → "near-real-time", "custom, enterprise-grade" → "enterprise-grade, custom-built", and "designed from the ground up" → "engineered from the ground up". No semantic changes.

### 1.2 Business Objectives

v1.1.0 adds a fourth column **"Measurement Method"** to the objectives table with concrete verification methods (Feature completeness audit, End-to-end alert timing tests, User workflow analysis, Security penetration testing, OpenSearch slow query logs).

### 1.3 Scope

| Change | v1.0.0 | v1.1.0 |
|--------|--------|--------|
| Format | Bullet list with dashes | ✅/❌ checkmark format |
| **NEW in 1.1.0** | — | ✅ Real-time WebSocket notifications for alert events |
| **NEW in 1.1.0** | — | ❌ Network topology auto-discovery (out of scope) |

### 1.4 Assumptions & Constraints

v1.1.0 converts dash-style list to numbered items with bold sub-headers (**OpenSearch Availability**, **Index Stability**, **Docker Requirements**, **External Integrations**, **Site Name Configuration**). Content unchanged.

---

## 2. Glossary (Section 2)

| Change | Detail |
|--------|--------|
| **NEW in v1.1.0** | `Flow Correlation` — definition of bidirectional flow pairing using `flow.correlation_id` to link client→server and server→client traffic |

---

## 3. System Architecture (Section 3)

v1.1.0 minor reformatting of the ASCII architecture diagram. No structural changes to the defined tech stack.

---

## 4. Data Sources (Section 4)

Content unchanged except formatting — v1.1.0 uses numbered sections rather than inline paragraphs for assumptions.

---

## 5. Data Dictionary (Section 5) — MAJOR CHANGES

### 5.1 AppID Flow Index — `fortigate-appid-flow-*`

| Change | Field | Detail |
|--------|-------|--------|
| **RENAMED** | `conversation_id` → `flow.correlation_id` | Bidirectional flow identifier. v1.1.0 specifies format: `{sorted_ip_1}:{sorted_port_1}-{sorted_ip_2}:{sorted_port_2}-{protocol}` |
| **NEW** | `flow.as_country` | AS country derived from BGP/GeoIP lookup (pipeline-dependent) |
| **NEW** | `flow.destination_organization` | Destination organization name (pipeline-dependent) |
| **NEW** | `sessions` | Aggregation-derived metric — count of flow documents per `flow.correlation_id` bucket (computed at query time, no pipeline needed) |

**Critical Note (v1.1.0):** `flow.bytes_human` is marked `[NO-AGG]` — pre-formatted keyword string, must not be used in any aggregation query. Byte-sum calculations must operate on `flow.client.bytes + flow.server.bytes`.

### 5.2 SNMP / Telegraf (Section 5.2)

v1.1.0 adds a new Section 5.2.1 sub-header for "SD-WAN SLA" measurement, renumbering the existing SSL VPN and IPsec sections accordingly. No field changes within these sections.

### Pipeline-Dependent Fields (v1.1.0 Only)

v1.1.0 explicitly labels `flow.as_country`, `flow.destination_organization`, and `flow.correlation_id` as **pipeline-dependent** — requiring upstream validation before features referencing them are released to production. Features dependent on absent fields must render graceful empty states rather than query errors.

---

## 6. Functional Requirements (Section 6) — MAJOR CHANGES

### FR-01: Overview Dashboard

| Change | Detail |
|--------|--------|
| Panels | v1.0.0: 9 panels (P01-A through P01-I). v1.1.0: **11 panels** |
| **NEW P01-J** | Active VPN Users Table — combined SSL + IPsec table using `top_hits` per site |
| **NEW P01-K** | Top Destination Organizations — horizontal bar chart using `terms` agg on `flow.destination_organization` |

Acceptance criteria updated: "9 panels" → "11 panels", v1.1.0 adds "All KPI cards display trend indicators (↑/↓) compared to the previous time window."

### FR-02: Traffic Flow View — COMPLETE RESTRUCTURING

| v1.0.0 | v1.1.0 |
|--------|--------|
| Single flat page with 9 components (TF-01 through TF-09) | **5 sub-pages** with dedicated routing |
| TF-01: Top Applications Bar Chart | → **Sub-page 2.1 (Summary)**: TFS-01 Top N Table + TFS-02 Throughput Timeline |
| TF-02: Application Category Donut | → **RETIRED** |
| TF-03: Sankey Diagram (3-node: Zone→App→Egress) | → **Sub-page 2.4 (Sankey)**: TSK-01 4-node composite (Zone→App→Egress→**AS Country**) |
| TF-04: Throughput Timeline | → **Sub-page 2.2 (Timeseries)**: TSVA-01 Stacked Area in **Mbps** with brush/zoom |
| TF-05: Top Client IPs | → **RETIRED** |
| TF-06: Top Server IPs | → **RETIRED** |
| TF-07: Protocol Distribution | → **RETIRED** |
| TF-08: Egress Interface Breakdown | → **RETIRED** |
| TF-09: Flow Conversations Table (`conversation_id`) | → **Sub-page 2.5 (Conversations)**: TCD-01 Volume Timeline + TCD-02 Data Table using `flow.correlation_id` |
| *(not present)* | → **Sub-page 2.3 (Custom Filter)**: accepts manual filter inputs (Client IP, Server IP, App Name, etc.) |

**Key v1.1.0 Field Rename:** `conversation_id` → `flow.correlation_id` across all traffic queries.

**Mbps Calculation (NEW in v1.1.0):** TSVA-01 stacked area chart computes Mbps in the **backend**: `(Sum of bytes × 8) / (Time Interval in Seconds)`. Raw byte sums never returned to frontend.

### FR-17: SNMP Interface Stats View — BRAND NEW

| Attribute | Detail |
|-----------|--------|
| **Priority** | P1 — Should Have |
| **Data Source** | `telegraf-index*`, `measurement_name: "interface_stats"` |
| **Status** | Blocked pending pipeline validation |
| **Components** | SNMP-01: Interface Selector, SNMP-02: Status Badges (Up/Down), SNMP-03: Utilization Line Chart (In/Out %), SNMP-04: Interface Detail Table |
| **Rule Q-06** | Exact `term` filter on `measurement_name` — NO wildcards |

### FR-03 through FR-16

No structural changes. v1.1.0 includes formatting refinements and numbered step formatting for deployment instructions.

---

## 7. Non-Functional Requirements (Section 7)

No content changes. v1.1.0 reformats with numbered sub-items and adds explicit "Requirement / Target / Measurement" columns to the performance table.

---

## 8. Anti-Bloatware Policy (Section 8) — STRENGTHENED

| Change | Detail |
|--------|--------|
| **NEW Q-06** | `measurement_name` exact filter upgraded to **"Non-Negotiable for telegraf-index*"** — wildcard or prefix queries on `measurement_name` are prohibited |
| **NEW rule** | Backend API responses must contain only fields required by the frontend consumer — not raw OpenSearch documents |
| **NEW rule** | No hardcoded credentials, IP addresses, or environment-specific values in source files |
| Formatting | v1.1.0 adds numbered rule table format for clarity |

---

## 9. UI/UX Guidelines (Section 9)

### 9.2 Page Layout Structure

| v1.0.0 | v1.1.0 |
|--------|--------|
| Flat sidebar: Traffic Flow as a single link | **Collapsible parent menu** "Traffic Flow" with 5 sub-links (Summary, Timeseries, Custom Filter, Sankey, Conversations) |
| *(not present)* | NEW nav item: **"SNMP Interfaces"** under monitoring pages |

### 9.4 Chart Standards

v1.1.0 adds explicit chart standards:
- All chart tooltips must display formatted values with appropriate units
- Charts must display an explicit empty state component ("No data for selected time range")
- Chart legends must be below the chart area, not overlaid
- Charts must not block page render (Suspense/SWR loading with Skeleton placeholder)

---

## 10. Deployment (Section 10)

v1.0.0 includes inline YAML and bash code blocks for `docker-compose.yml` and first-run sequence. v1.1.0 references them by line numbers without embedding the full blocks.

---

## 11. Security (Section 11)

No content changes. Formatting only (adding `python` language annotation to code blocks).

---

## 12. API Design (Section 12)

| Change | v1.0.0 | v1.1.0 |
|--------|--------|--------|
| Traffic endpoints | Single `GET /api/v1/traffic` returning all 9 components | **5 sub-routes**: `GET /api/v1/traffic/summary`, `timeseries`, `custom-filter`, `sankey`, `conversations` |
| Raw data | `GET /api/v1/traffic/raw` | Unchanged |
| **NEW** | — | `GET /api/v1/snmp/interfaces` |
| **NEW** | — | `GET /api/v1/notifications` (FR-10) |
| HTTP Status codes | — | v1.1.0 adds `400 PAGINATION_LIMIT_EXCEEDED` and `503 DEPENDENCY_UNAVAILABLE` |

---

## 13. Acceptance Criteria (Section 13)

v1.1.0 adds 3 new acceptance tests:

| Test ID | Description |
|---------|-------------|
| AT-17 | `docker compose up -d --build` brings all services to healthy state within 120 seconds |
| AT-18 | `alembic upgrade head` + `seed_superadmin.py` complete without error |
| **AT-19** | `flow.correlation_id` correctly groups bidirectional flows; conversation table shows accurate byte/packet totals |

Adds performance (PT-01 through PT-05) and security (ST-01 through ST-05) test tables.

---

## 14. Appendix B — Revision History

| Version | PRD_Doc1.md | PRD_by_Qwen.md |
|---------|-------------|----------------|
| 1.0.0 | ✅ Present | ✅ Present |
| 1.1.0 | ❌ Not present | ✅ Present — "Updated conversation_id to flow.correlation_id; enhanced Flow Conversations Table; improved document structure; added acceptance criteria for flow correlation" |

---

## Summary of Impact

| Category | Count | Severity |
|----------|-------|----------|
| New functional requirements | 3 (FR-17 SNMP, P01-J, P01-K) | P0/P1 |
| Field renames | 1 (`conversation_id` → `flow.correlation_id`) | P0 — breaking |
| New OpenSearch fields | 3 (flow.as_country, flow.destination_organization, flow.correlation_id) | Pipeline-dependent |
| Retired components | 5 (TF-02, TF-05, TF-06, TF-07, TF-08) | Code removal |
| New API endpoints | 7 (5 traffic sub-routes + snmp + notifications) | P0 |
| Navigation restructure | 1 (Traffic Flow collapsible menu) | UI change |
| Anti-bloatware rule upgrade | 1 (Q-06 strengthened to Non-Negotiable) | Policy |
