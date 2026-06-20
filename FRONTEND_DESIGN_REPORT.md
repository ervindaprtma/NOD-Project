# NOD (Network Observability Dashboard) — Frontend Design Report

> **Generated:** 2026-06-20
> **Framework:** Next.js 14+ (App Router)
> **Total Source Files:** 26
> **Total Lines of Code:** ~6,800+

---

## Table of Contents

1. [Project Overview & Architecture](#1-project-overview--architecture)
2. [Design System](#2-design-system)
3. [Component Library](#3-component-library)
4. [Page-by-Page Analysis](#4-page-by-page-analysis)
5. [Data Flow Architecture](#5-data-flow-architecture)
6. [Visual Design Patterns & Conventions](#6-visual-design-patterns--conventions)
7. [Type System Overview](#7-type-system-overview)
8. [Known Issues & Technical Debt](#8-known-issues--technical-debt)
9. [File Inventory](#9-file-inventory)

---

## 1. Project Overview & Architecture

### 1.1 What is NOD?

NOD (Network Observability Dashboard) is a comprehensive network monitoring and observability platform frontend. It provides real-time visibility into network traffic, VPN connections, SD-WAN SLA metrics, device resources, and security alerts across multiple sites (DC, DRC, Office).

### 1.2 Technology Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 14+ (App Router) |
| Language | TypeScript |
| Styling | Tailwind CSS (shadcn/ui CSS variable pattern) |
| Charts | Recharts (line/area/bar), d3-sankey (flow diagrams) |
| State/Data | SWR (stale-while-revalidate) |
| Authentication | JWT (access + refresh tokens, localStorage) |
| Theming | next-themes (class strategy) |
| UI Primitives | Radix UI (Tabs) |
| Icons | Lucide React, Emoji |
| Date/Time | Native HTML `datetime-local` inputs (no date library) |

### 1.3 Routing Architecture

The project uses Next.js 14 App Router with the following structure:

```
/                          → Redirects to /dashboard/overview
/login                     → Auth-protected login page
/dashboard                 → Authenticated shell (sidebar + header)
/dashboard/overview        → KPI overview
/dashboard/traffic         → Internet traffic analysis
/dashboard/traffic-inbound → Inbound VIP traffic analysis
/dashboard/traffic-internal→ Internal traffic analysis
/dashboard/sdwan           → SD-WAN SLA monitoring
/dashboard/resources       → Device resources & interface bandwidth
/dashboard/reports         → Report generation & download
/dashboard/vpn             → VPN user monitoring
/dashboard/raw-data        → Raw flow records
/dashboard/alerts          → Alert rules & history
/dashboard/users           → User management (admin)
/dashboard/settings        → User settings
/dashboard/activity-logs   → Audit trail (superadmin only)
```

### 1.4 Layout Hierarchy

```
app/layout.tsx (25 lines)
  └── ThemeProvider (next-themes wrapper)
      └── app/page.tsx (5 lines — redirect)
      └── (auth)/login/page.tsx (111 lines — standalone)
      └── dashboard/layout.tsx (287 lines — sidebar + header)
          ├── Sidebar (collapsible, 240px/64px)
          ├── Header (hamburger, theme, notifications, profile)
          └── <main> → child pages
```

---

## 2. Design System

### 2.1 CSS Variables (`globals.css` — 113 lines)

The design system follows the **shadcn/ui CSS variable pattern** with full light/dark mode support. All colors are defined as HSL values and consumed via `hsl(var(--variable))`.

#### Core Color Tokens

| Variable | Light Mode | Dark Mode | Usage |
|---|---|---|---|
| `--background` | `0 0% 100%` | `0 0% 3.9%` | Page background |
| `--foreground` | `0 0% 3.9%` | `0 0% 98%` | Primary text |
| `--card` | `0 0% 100%` | `0 0% 3.9%` | Card backgrounds |
| `--card-foreground` | `0 0% 3.9%` | `0 0% 98%` | Card text |
| `--primary` | `0 0% 9%` | `0 0% 98%` | Buttons, primary actions |
| `--primary-foreground` | `0 0% 98%` | `0 0% 9%` | Text on primary |
| `--muted` | `0 0% 96.1%` | `0 0% 14.9%` | Subtle backgrounds |
| `--muted-foreground` | `0 0% 45.1%` | `0 0% 63.9%` | Secondary text |
| `--border` | `0 0% 89.8%` | `0 0% 14.9%` | Borders |
| `--ring` | `0 0% 3.9%` | `0 0% 83.1%` | Focus rings |
| `--destructive` | `0 84.2% 60.2%` | `0 62.8% 30.6%` | Destructive actions |

#### Chart Color Tokens

| Variable | HSL Value | Approximate Color |
|---|---|---|
| `--chart-1` | `220 70% 50%` | Blue |
| `--chart-2` | `0 72% 51%` | Red |
| `--chart-3` | `142 71% 45%` | Green |
| `--chart-4` | `160 84% 39%` | Emerald |
| `--chart-5` | `262 83% 58%` | Purple |

### 2.2 Status Badge Classes

| Class | Light Mode | Dark Mode | Usage |
|---|---|---|---|
| `.badge-success` | Green bg/text | Dark green | Up, active, completed |
| `.badge-danger` | Red bg/text | Dark red | Down, critical, failed |
| `.badge-warning` | Amber bg/text | Dark amber | Pending, warning |
| `.badge-info` | Blue bg/text | Dark blue | Informational, running |
| `.badge-neutral` | Gray bg/text | Dark gray | Unknown, default |

### 2.3 Typography & Spacing

- **Font system:** Default Next.js system font stack via Tailwind
- **Card pattern:** `bg-card border rounded-lg shadow-sm dark:ring-1 dark:ring-white/20`
- **Spacing:** Tailwind's default 4px grid (`p-4`, `gap-4`, `mb-6`, etc.)
- **Border radius:** `rounded-lg` for cards, `rounded-md` for buttons/inputs, `rounded-sm` for badges

### 2.4 Print Styles

The `globals.css` includes `@media print` rules that:
- Hide the sidebar navigation (`nav`, `[role="navigation"]`)
- Hide the header bar
- Hide all buttons
- Force white background for print output

### 2.5 Theme Configuration (`ThemeProvider.tsx` — 28 lines)

```typescript
// next-themes configuration
attribute = "class"     // Toggles via .dark class on <html>
defaultTheme = "system" // Respects OS preference
storageKey = "nod-theme"
```

This persists user preference in `localStorage` under the key `"nod-theme"`.

### 2.6 Theme Toggle (`ThemeToggle.tsx` — 46 lines)

- Renders sun/moon icon toggle using Lucide React
- **Hydration-safe:** Uses a `mounted` state flag to prevent hydration mismatch — renders nothing on server
- Calls `useTheme()` from `next-themes` to read/write preference
- Placed in the dashboard header for global access

---

## 3. Component Library

### 3.1 `AreaChart.tsx` — Recharts Wrapper (165 lines)

**Location:** `src/components/charts/AreaChart.tsx`

A reusable chart component wrapping Recharts' `ResponsiveContainer` + `AreaChart`.

#### Props

| Prop | Type | Default | Description |
|---|---|---|---|
| `data` | `any[]` | required | Array of data objects |
| `categories` | `string[]` | required | Keys to plot as lines/areas |
| `index` | `string` | required | X-axis data key |
| `colors` | `string[]` | `['blue','red']` | Color names mapped via `resolveColor()` |
| `valueFormatter` | `(v: number) => string` | `toLocaleString` | Format Y-axis values |
| `showLegend` | `boolean` | `true` | Show legend |
| `showGridLines` | `boolean` | `true` | Show grid |
| `showXAxis` | `boolean` | `true` | Show X-axis labels |
| `showYAxis` | `boolean` | `true` | Show Y-axis labels |
| `autoMinValue` | `boolean` | `false` | Auto-scale Y-axis minimum |
| `curveType` | `string` | `'monotone'` | Recharts curve type |
| `showGradient` | `boolean` | `false` | Gradient fill under areas |
| `tickGap` | `number` | `5` | Space between X-axis ticks |
| `yAxisWidth` | `number` | `40` | Y-axis label width |

#### Color Resolution

The `resolveColor()` function maps color name strings to HSL CSS variables:
- `'blue'` → `hsl(var(--chart-1))`
- `'red'` → `hsl(var(--chart-2))`
- `'green'` → `hsl(var(--chart-3))`
- `'emerald'` → `hsl(var(--chart-4))`
- `'purple'` → `hsl(var(--chart-5))`

#### Tooltip

Custom `DefaultTooltip` renders a rounded card with colored dots per category and formatted values.

#### Layout

The component uses a parent `<div>` with a controlled height and wraps Recharts' `ResponsiveContainer` to handle responsive sizing.

---

### 3.2 `TimeRangePicker.tsx` — Custom Date Picker (183 lines)

**Location:** `src/components/panels/TimeRangePicker.tsx`

A custom date/time range picker built with **native HTML `<input type="datetime-local">`** — no external date library dependency.

#### Features

- **Two inputs:** Start time ("From") and End time ("To")
- **Validation:**
  - Future dates are blocked
  - "To" must be after "From"
  - Empty fields are not allowed
- **24-hour warning:** If the selected range exceeds 24 hours, a warning dialog is displayed
- **Z-index layering:**
  - `z-40` — Backdrop overlay
  - `z-50` — Dialog content
  - `z-60/70` — Warning dialog (above everything)
- **Backdrop:** Clicking outside the dialog closes it

#### Design

- Positioned as a floating panel with backdrop overlay
- Uses Tailwind for all styling (no CSS modules)
- Warning dialog uses a red accent border and icon

---

### 3.3 `ThemeToggle.tsx` — Theme Switcher (46 lines)

**Location:** `src/components/ThemeToggle.tsx`

- Simple button component toggling between light/dark themes
- Uses Lucide React icons: `Sun` and `Moon`
- **Hydration-safe pattern:** Tracks `mounted` state to avoid server/client mismatch (renders empty string before mount)
- Delegates to `useTheme()` hook from `next-themes`

---

### 3.4 `ThemeProvider.tsx` — Theme Context (28 lines)

**Location:** `src/components/ThemeProvider.tsx`

- Wraps `NextThemesProvider` with specific configuration
- Placed at the root layout level to provide theme context to all children
- Configuration: `attribute="class"`, `defaultTheme="system"`, `storageKey="nod-theme"`

---

## 4. Page-by-Page Analysis

### 4.1 Root Page (`app/page.tsx` — 5 lines)

**Route:** `/`

A simple redirect page that sends users to `/dashboard/overview`. This is the application entry point — an unauthenticated user is redirected to login, while authenticated users go to the overview dashboard.

---

### 4.2 Login Page (`app/(auth)/login/page.tsx` — 111 lines)

**Route:** `/login`
**Layout:** Standalone (no sidebar/header — uses a separate layout group `(auth)`)

#### Visual Design
- Centered card layout (`max-w-md mx-auto`)
- Card styling: `bg-card border shadow-sm`
- NOD branding: Network icon from Lucide React with "NOD" title text
- Subtitle: "Network Observability Dashboard"

#### Form
- **Fields:** Username, Password
- **Error display:** Inline error message in red below the form
- **Submit:** Calls login API, stores JWT via `setAccessToken()`, redirects to `/dashboard/overview`

#### Authentication Flow
1. User submits credentials
2. API returns `TokenResponse` (access + refresh tokens)
3. Tokens stored in localStorage via `setAccessToken()`
4. Client-side redirect to `/dashboard/overview`

---

### 4.3 Dashboard Layout (`app/dashboard/layout.tsx` — 287 lines)

**Route:** `/dashboard/*` (wraps all dashboard pages)

This is the **core application shell** — the authenticated layout with sidebar navigation, header, and content area.

#### Layout Structure

```
┌──────────┬──────────────────────────────────┐
│          │  Header (hamburger | theme | bell │
│ Sidebar  │       | profile dropdown)         │
│ (240px   ├──────────────────────────────────┤
│  / 64px) │                                  │
│          │  <main> Child Page Content </main>│
│          │                                  │
└──────────┴──────────────────────────────────┘
```

#### Sidebar
- **Width:** 240px (expanded) / 64px (collapsed)
- **Background:** `bg-card border-r`
- **13 Navigation Items** with emoji icons:

| Icon | Route | Label |
|---|---|---|
| ◉ | /dashboard/overview | Overview |
| 🌐 | /dashboard/traffic | Traffic Internet |
| ↘ | /dashboard/traffic-inbound | Traffic Inbound |
| ⇄ | /dashboard/traffic-internal | Traffic Internal |
| ⏱ | /dashboard/sdwan | SD-WAN SLA |
| ⊞ | /dashboard/resources | Resources |
| 🔒 | /dashboard/vpn | VPN |
| ☰ | /dashboard/raw-data | Raw Data |
| ⚠ | /dashboard/alerts | Alerts |
| 📄 | /dashboard/reports | Reports |
| 👥 | /dashboard/users | Users |
| 📋 | /dashboard/activity-logs | Activity Logs |
| ⚙ | /dashboard/settings | Settings |

- **Role-based visibility:** Activity Logs is hidden from non-superadmin users
- **Collapse toggle:** Hamburger button in the header

#### Header
- **Left:** Hamburger menu toggle (expand/collapse sidebar)
- **Center:** Page title (dynamic based on current route)
- **Right:** ThemeToggle component + Notification bell (with unread count badge) + User profile dropdown

#### Authentication Guard
- On mount, validates the JWT token
- If invalid/expired, redirects to `/login`
- Token refresh attempted automatically before redirect

#### Notifications
- Polls `/api/v1/notifications` every 30 seconds
- Displays unread count as a badge on the bell icon
- Mark-all-read functionality

---

### 4.4 Overview Page (`app/dashboard/overview/page.tsx` — 530 lines)

**Route:** `/dashboard/overview`

The **main landing page** after login, displaying a comprehensive network health overview across all sites.

#### Sub-Components (defined inline)

| Component | Purpose |
|---|---|
| `KpiCard` | Summary metric card with left border color |
| `ClickCard` | Clickable card that navigates to a related page |
| `BarRow` | Single horizontal bar with rank number, label, bar, and value |
| `MiniGauge` | Circular gauge for CPU/Memory percentage |
| `SkeletonBars` | Loading skeleton for bar chart areas |
| `SkeletonCard` | Loading skeleton for card areas |
| `EmptyText` | "No data available" placeholder |

#### Row 1: KPI Summary Cards (5 cards, full width)

| Card | Metric | Left Border | Navigates To |
|---|---|---|---|
| SSL VPN | Active SSL VPN users | Blue | `/dashboard/vpn` |
| IPsec VPN | Active IPsec VPN users | Emerald | `/dashboard/vpn` |
| Devices | Total device count | Amber | `/dashboard/resources` |
| HA Cluster | HA cluster status | Red | `/dashboard/resources` |
| Alerts | Active alert count | Slate | `/dashboard/alerts` |

#### Row 2: Top Applications (3-column, per site)

Three columns: **DC**, **DRC**, **Office**

Each column displays a horizontal bar chart showing the top applications ranked by traffic volume. Each bar includes:
- Rank number
- Application name
- Colored progress bar
- Traffic value

#### Row 3: Top AS Organizations (3-column, per site)

Three columns: **DC**, **DRC**, **Office**

Horizontal bar charts using **emerald** color bars. Shows top autonomous system organizations by traffic volume.

#### Row 4: Device Health (3-column, per site)

Three columns: **DC**, **DRC**, **Office**

Each device card displays:
- Hostname
- Serial number
- **MiniGauge** for CPU utilization (percentage)
- **MiniGauge** for Memory utilization (percentage)
- Session count
- RAM capacity

**DC-specific addition:** HA Cluster Status panel at the bottom showing:
- HA mode (Active-Passive / Active-Active)
- Health status
- Individual member status

#### Row 5: WAN/MPLS Bandwidth + SD-WAN Status (2-column)

**Left column — WAN/MPLS Bandwidth:**
- Site selector dropdown (to filter by specific site)
- Interface cards showing:
  - Interface name
  - In Mbps (blue background)
  - Out Mbps (orange background)
- Sort order: WAN interfaces first (LinkNet, iForte, LDP), then MPLS interfaces (LinkNet, iForte)

**Right column — SD-WAN Link Status:**
- Per-link status cards
- Current bandwidth metrics

#### Row 6: Inbound VIP (2-column)

Two columns: **DC**, **DRC**

Horizontal bar charts using **violet** color bars showing inbound VIP traffic distribution.

#### Row 7: Top Customer AS — Inbound VIP (2-column)

Two columns: **DC**, **DRC**

Horizontal bar charts using **cyan** color bars showing top customer autonomous systems for inbound VIP traffic.

#### Data Fetching

- Uses SWR with multiple API calls per site
- Time range refresh via `useEffect` + `setInterval` when active preset is selected
- Each section independently loading/error/empty states

---

### 4.5 Traffic Internet Page (`app/dashboard/traffic/page.tsx` — 1140 lines)

**Route:** `/dashboard/traffic`

The **largest page** in the application — comprehensive internet traffic analysis.

#### Controls Bar

| Control | Options |
|---|---|
| Site selector | DC, DRC, Office |
| Time presets | 15m, 1h, 2h, 4h, 12h, 24h |
| Custom time range | DateTime picker |
| Refresh interval | Off, 15s, 30s, 60s |
| Filter bar (collapsible) | application, category, client_ip, server_ip, protocol, dst_port |

#### Tab Navigation

Uses **Radix Tabs** with 2 tabs:
1. **Overview** — Charts and tables
2. **Sankey Diagram** — Flow visualization

Tab styling:
- Container: `bg-muted/40`
- Active trigger: `bg-background shadow-sm`
- Inactive trigger: gray text with hover

#### Overview Tab Layout

**ROW 1 — Throughput Overview (2 wide cards):**

| Card | Chart Type | Description |
|---|---|---|
| Total Throughput Over Time | AreaChart | Sum of all application throughputs per time bucket, displayed in Mbps |
| App Throughput Stacked Bar | Recharts BarChart | Per-application throughput as stacked bars |

**Derived Data Calculations:**
- `throughputTimeline`: Sums all application values per time bucket → converts to Mbps
- `stackedBarData`: Per-application Mbps values for each time bucket
- `bucketSeconds`: Dynamic calculation = `rangeSec / 30` (keeps approximately 30 bars on screen)

**ROW 2 — Top Rankings (2 medium cards):**

| Card | Content |
|---|---|
| Top Applications | Horizontal bar chart with rank numbers |
| Top Categories | Horizontal bar chart with category names |

**ROW 3 — Geographic/Network (2 medium cards):**

| Card | Content |
|---|---|
| Top Destination AS Org | Horizontal bar chart |
| Top Destination Countries | Horizontal bar chart |

**ROW 4 — Endpoints (2 medium cards):**

| Card | Content |
|---|---|
| Client IPs | Horizontal bar chart |
| Server IPs | Horizontal bar chart |

**ROW 5 — Protocol & Interface (2 medium cards):**

| Card | Content |
|---|---|
| Protocol Distribution | Horizontal bar chart |
| Top Egress Interfaces | Horizontal bar chart |

**ROW 6 — Source AS (full width):**

| Card | Content |
|---|---|
| Top Source AS Org | Horizontal bar chart (full width) |

**ROW 7 — Raw Data (full width):**

| Card | Content |
|---|---|
| Raw Data Table | Paginated, sortable table of flow records |

#### Sankey Tab

- Upload flow diagram (d3-sankey)
- Download flow diagram (d3-sankey)
- Visualizes source → destination → application flow paths

#### Data Fetching

5 SWR keys:
1. `traffic/summary` — Summary statistics
2. `traffic/chart` — Time series data
3. `traffic/table` — Raw flow records
4. `traffic/sankey-upload` — Upload Sankey data
5. `traffic/sankey-download` — Download Sankey data

---

### 4.6 Traffic Inbound Page (`app/dashboard/traffic-inbound/page.tsx` — 545 lines)

**Route:** `/dashboard/traffic-inbound`

Analyzes **inbound VIP traffic** — traffic coming into the network's virtual IP addresses.

#### Controls

Same pattern as Traffic Internet, with one difference:
- **Site selector:** DC, DRC only (Office is excluded)

#### Tab Navigation

Same 2-tab pattern: Overview + Sankey Diagram

#### Overview Tab Layout

**ROW 1 — Throughput & Services (2 wide cards):**

| Card | Chart Type | Description |
|---|---|---|
| Throughput Timeline | AreaChart | Inbound throughput over time |
| Inbound Service Throughput | Horizontal bars | Top inbound services ranked |

**ROW 2 — Network/Geographic (2 medium cards):**

| Card | Content |
|---|---|
| Top Client AS | Horizontal bar chart |
| Top Destination Countries | Horizontal bar chart |

**ROW 3 — Endpoints (2 medium cards):**

| Card | Content |
|---|---|
| Client IPs | Horizontal bar chart |
| Server IPs | Horizontal bar chart |

**ROW 4 — Protocol & Interface (2 medium cards):**

| Card | Content |
|---|---|
| Protocol Distribution | Horizontal bar chart |
| Top Egress Interfaces | Horizontal bar chart |

**ROW 5 — Source AS (full width):**

| Card | Content |
|---|---|
| Top Source AS Org | Horizontal bar chart (full width) |

**ROW 6 — Raw Data (full width):**

| Card | Content |
|---|---|
| Raw Data Table | Paginated, sortable table |

#### Data Fetching

5 SWR keys (same pattern as Traffic Internet):
1. `inbound/summary`
2. `inbound/chart`
3. `inbound/table`
4. `inbound/sankey-upload`
5. `inbound/sankey-download`

---

### 4.7 Traffic Internal Page (`app/dashboard/traffic-internal/page.tsx` — 367 lines)

**Route:** `/dashboard/traffic-internal`

Analyzes **internal network traffic** — traffic flowing within the organization's network.

#### Controls

| Control | Options |
|---|---|
| Site selector | DC, DRC, Office |
| Traffic path selector | All Paths, Intra-LAN, Inter-Site |
| Time/filter/refresh | Same pattern as other traffic pages |

The **traffic path selector** is unique to this page, allowing filtering between:
- **All Paths:** Both intra-LAN and inter-site traffic
- **Intra-LAN:** Traffic staying within the same LAN segment
- **Inter-Site:** Traffic flowing between different sites

#### Tab Navigation

Same 2-tab pattern: Overview + Sankey Diagram

#### Overview Tab Layout

**ROW 1 — Top Services (full width):**

| Card | Content |
|---|---|
| Top Services | Horizontal bar chart (full width) |

**ROW 2 — Endpoints & Interfaces (4-column):**

| Card | Content |
|---|---|
| Client IPs | Horizontal bar chart |
| Server IPs | Horizontal bar chart |
| Ingress Interfaces | Horizontal bar chart |
| Egress Interfaces | Horizontal bar chart |

**ROW 3 — Protocol & Throughput (2 medium cards):**

| Card | Content |
|---|---|
| Protocol Distribution | Horizontal bar chart |
| Throughput Timeline | AreaChart |

**ROW 4 — Raw Data (full width):**

| Card | Content |
|---|---|
| Raw Data Table | Paginated, sortable table |

#### Data Fetching

4 SWR keys:
1. `internal/summary`
2. `internal/chart`
3. `internal/table`
4. `internal/sankey`

---

### 4.8 SD-WAN SLA Page (`app/dashboard/sdwan/page.tsx` — 552 lines)

**Route:** `/dashboard/sdwan`

Monitors **SD-WAN link quality** — latency, jitter, packet loss per WAN link.

#### Site Selector

- **DC** — Blue badge
- **DRC** — Emerald badge
- **Office** — Amber badge

#### Expandable Sections

5 sections, each with a compact card view that expands to full-width detail:

| # | Section | Content |
|---|---|---|
| 1 | Link Status | Per-site link cards with status badges (Up=green, Down=red) |
| 2 | Summary Table | Latency, jitter, packet loss per link in tabular format |
| 3 | Latency Timeline | AreaChart with blue lines per link |
| 4 | Jitter Timeline | AreaChart with orange lines, formatted in ms |
| 5 | Packet Loss Timeline | AreaChart with red lines, formatted as percentage |

#### Section Details

**Link Status Cards:**
- Per-link card showing link name, status badge, and current metrics
- Status: `Up` (green badge) or `Down` (red badge)

**Summary Table:**
- Columns: Link name, Latency (ms), Jitter (ms), Packet Loss (%)
- Each metric may have SLA threshold indicators

**Latency Timeline (AreaChart):**
- One line per WAN link
- Color: blue
- Y-axis: milliseconds

**Jitter Timeline (AreaChart):**
- One line per WAN link
- Color: orange
- Y-axis: milliseconds (uses `formatAlwaysMs` formatter)

**Packet Loss Timeline (AreaChart):**
- One line per WAN link
- Color: red
- Y-axis: percentage

**SLA Thresholds:** Reference lines shown on charts to indicate SLA breach points.

#### Expanded View

When a section card is clicked:
- Section expands to full width
- Back button appears to return to grid view
- Only one section can be expanded at a time

#### Data Fetching

1 SWR key: `sdwan/sla`

---

### 4.9 Resources Page (`app/dashboard/resources/page.tsx` — 793 lines)

**Route:** `/dashboard/resources`

Monitors **device health** and **interface bandwidth** across sites.

#### Tab Navigation

2 tabs via Radix Tabs:
1. **Resource Usage** — Device health and resource timelines
2. **Interface Bandwidth** — Network interface monitoring

#### Resource Usage Tab

**Expandable Sections:** `deviceStatus` and `timeline`

**Device Status Cards (2-column grid):**
Each device card displays:
- Hostname
- Serial number
- Sync status badge
- CPU utilization gauge
- Memory utilization gauge
- Session count
- RAM capacity

**HA Status Card (DC only):**
- HA mode (Active-Passive/Active-Active)
- Current role (Primary/Secondary)
- Priority
- Sync status

**Resource Timeline (3 AreaCharts):**

| Chart | Content | Colors |
|---|---|---|
| CPU Timeline | Per-device CPU usage over time | One line per device |
| Memory Timeline | Per-device memory usage over time | One line per device |
| Sessions Timeline | Per-device session count over time | One line per device |

**Device Filter:** Dropdown to filter timeline charts by specific device.

#### Interface Bandwidth Tab

**Interface Cards:**
Each card shows:
- Interface name (`ifName`)
- Link speed
- In Mbps
- Out Mbps
- Operational status
- Mini timeline AreaChart

**Sort Order:** WAN interfaces first (LinkNet, iForte, LDP), then MPLS interfaces (LinkNet, iForte)

#### Data Fetching

3 SWR keys:
1. `resources` — Device status and resource data
2. `ha/status` — HA cluster status
3. `interface-stats` — Interface statistics and timelines

---

### 4.10 Reports Page (`app/dashboard/reports/page.tsx` — 698 lines)

**Route:** `/dashboard/reports`

Generates and manages **network reports**.

#### Report Types (8 types)

| ID | Description |
|---|---|
| R-01 | Traffic Summary Report |
| R-02 | Bandwidth Usage Report |
| R-03 | Security Events Report |
| R-04 | VPN Usage Report |
| R-05 | SD-WAN Performance Report |
| R-06 | Device Health Report |
| R-07 | Alert History Report |
| R-08 | User Activity Report |

#### Report Generation Flow

1. Select report type (card with description)
2. Select site (DC, DRC, Office)
3. Select sections to include (checkboxes)
4. Click "Generate" button
5. POST to `/api/v1/reports/generate`

#### Job List

Displays previously generated reports with:
- Report type and site
- Status badges:
  - `pending` — Amber
  - `running` — Blue
  - `completed` — Green
  - `failed` — Red

#### Actions

| Action | Method | Notes |
|---|---|---|
| Download | Fetch with auth headers → blob download | Works correctly |
| Preview | `window.open(url)` | **BROKEN** — No auth header sent, opens raw HTML |

#### Data Fetching

1 SWR key: `reports/list`

---

### 4.11 VPN Page (`app/dashboard/vpn/page.tsx`)

**Route:** `/dashboard/vpn`

Monitors **VPN connections** — both SSL VPN and IPsec VPN.

#### Content

- **VPN User Counts:** Per-site counts for SSL VPN and IPsec VPN
- **User Lists:** Tables showing:
  - Username
  - Connection duration
  - Bytes transferred
- **Charts:** VPN user timeline showing connection trends over time

---

### 4.12 Raw Data Page (`app/dashboard/raw-data/page.tsx`)

**Route:** `/dashboard/raw-data`

Displays **raw flow records** in a full-page table.

#### Table Columns

| Column | Description |
|---|---|
| Client IP | Source IP address |
| Server IP | Destination IP address |
| Application | Detected application |
| Bytes | Total bytes transferred |
| Packets | Total packet count |
| Sessions | Session count |

#### Pagination

Uses **`search_after` pagination** (Elasticsearch-style) for deep page navigation. This allows efficient pagination through potentially millions of flow records without offset-based performance issues.

---

### 4.13 Alerts Page (`app/dashboard/alerts/page.tsx`)

**Route:** `/dashboard/alerts`

Manages **alert rules** and displays **alert history**.

#### Alert Rules CRUD

| Operation | Description |
|---|---|
| Create | New alert rule with full configuration |
| Edit | Modify existing rule parameters |
| Delete | Remove alert rule |
| Enable/Disable | Toggle rule active state |

#### Severity Levels

| Level | Usage |
|---|---|
| INFO | Informational alerts |
| WARNING | Warning-level conditions |
| CRITICAL | Critical/failing conditions |

#### Alert Rule Configuration

Each rule defines:
- **Data source:** Which data stream to monitor
- **Metric field:** Specific metric to evaluate
- **Aggregation:** How to aggregate data (avg, max, min, count)
- **Condition:** Comparison operator (>, <, ==, etc.)
- **Threshold:** Trigger value

#### Alert History

Table displaying:
- Alert rule name
- Severity level
- Firing timestamp
- Duration
- Description

---

### 4.14 Users Page (`app/dashboard/users/page.tsx` — 368 lines)

**Route:** `/dashboard/users`

**User management** for administrators.

#### User Table

| Column | Description |
|---|---|
| Username | Login username |
| Full Name | Display name |
| Email | Contact email |
| Role | User role with badge |
| Status | Active/Inactive badge |
| Last Login | Timestamp of last login |

#### Role Badges

| Role | Badge Color |
|---|---|
| superadmin | Purple |
| admin | Blue |
| operator | Amber |
| viewer | Slate |

#### CRUD Operations

- **Create User:** Modal dialog with form fields
- **Edit User:** Modal dialog pre-filled with current values
- **Delete User:** Confirmation dialog
- **Toggle Status:** Active/Inactive switch

#### Protection Rules
- **Cannot delete superadmin** — Button disabled with tooltip
- **Cannot deactivate superadmin** — Toggle disabled

---

### 4.15 Settings Page (`app/dashboard/settings/page.tsx` — 257 lines)

**Route:** `/dashboard/settings`

**User profile settings** with 3 tabs.

#### Tab 1: Change Password

- Current password field
- New password field
- Confirm password field
- **Validation:**
  - Minimum 8 characters
  - Must match confirmation
- Submit button

#### Tab 2: Display Name

- Current display name field
- Update button

#### Tab 3: Appearance

- ThemeToggle component (Light/Dark/System)
- Integrated with next-themes for instant preview

---

### 4.16 Activity Logs Page (`app/dashboard/activity-logs/page.tsx` — 170 lines)

**Route:** `/dashboard/activity-logs`

**Audit trail** — only accessible to superadmin users.

#### Access Control

- **Superadmin only:** Other roles see an "Access Denied" message
- Role check performed on mount

#### Table Columns

| Column | Description |
|---|---|
| Timestamp | When the action occurred |
| Action | Type of action performed |
| Username | Who performed the action |
| Role | User's role |
| Source IP | IP address of the request |
| Details | Additional context |

#### Action Type Badges

| Action Type | Badge Color |
|---|---|
| login | Blue |
| user_created | Green |
| user_updated | Amber |
| user_deleted | Red |
| alert_triggered | Red |
| report_generated | Blue |
| settings_changed | Slate |

#### Auto-Refresh

Table auto-refreshes every 30 seconds to show new log entries.

---

## 5. Data Flow Architecture

### 5.1 Authentication Flow

```
Login Page
  → POST /auth/login { username, password }
  ← { access_token, refresh_token, user }
  → Store in localStorage (setAccessToken)

Every API Call:
  ensureValidToken()
    → Check token expiry (30s buffer)
    → If expired: POST /auth/refresh { refresh_token }
    → If refresh fails: redirect to /login
    → If valid: attach Authorization: Bearer <token>

Auto-Refresh on 401:
  apiFetch() intercepts 401 response
    → Attempt token refresh
    → Retry original request
    → If refresh fails: redirect to /login
```

### 5.2 API Layer (`lib/api.ts` — 171 lines)

#### Core Functions

| Function | Purpose |
|---|---|
| `setAccessToken(token)` | Store JWT in localStorage |
| `getAccessToken()` | Retrieve JWT from localStorage |
| `refreshAccessToken()` | Exchange refresh token for new access token |
| `ensureValidToken()` | Validate token freshness (30s buffer) |
| `apiFetch(url, options)` | Authenticated fetch wrapper with auto-refresh |
| `swrFetcher(url)` | SWR-compatible fetcher using apiFetch |

#### Token Management

- **Storage key:** localStorage (key not specified, likely `access_token`)
- **Expiry buffer:** 30 seconds before actual expiry
- **Refresh endpoint:** `/auth/refresh`
- **API base:** Empty string (proxied through Next.js rewrites)

#### Role-Based Access

| Function | Purpose |
|---|---|
| `getUserRole()` | Extracts role from stored JWT |
| `hasMinRole(minRole)` | Checks if user has at least the specified role |

Role hierarchy: `superadmin > admin > operator > viewer`

### 5.3 SWR Data Fetching Pattern

All data-fetching pages follow this consistent pattern:

```
1. Token Check
   └── Validate JWT exists and is fresh

2. SWR Key Construction
   └── Build API URL with query params (site, time range, filters)

3. SWR Fetch
   └── useSWR(key, swrFetcher, options)
   └── swrFetcher calls apiFetch() with JWT

4. Auto-Refresh Timer (optional)
   └── useEffect + setInterval when activePresetSeconds > 0
   └── Mutates SWR cache at interval

5. Derived Data
   └── useMemo() for computed values
   └── Examples: throughputTimeline, stackedBarData, bucketSeconds

6. Render States
   └── Loading: Skeleton components
   └── Error: Error message card
   └── Empty: "No data available" text
   └── Success: Charts and tables
```

### 5.4 SWR Configuration

```typescript
{
  refreshInterval: 0,        // Manual refresh via timer
  revalidateOnFocus: false,  // Don't refetch on window focus
  shouldRetryOnError: false, // Don't retry on API errors
}
```

### 5.5 State Management

The application uses **minimal client state**:
- **URL parameters** for site selection and filters
- **Local component state** (`useState`) for UI interactions (expanded sections, tab selection)
- **SWR cache** for server data
- **localStorage** for auth tokens and theme preference
- **No global state library** (no Redux, Zustand, etc.)

### 5.6 Constants (`lib/constants.ts` — 95 lines)

#### Time Configuration

| Constant | Values |
|---|---|
| TIME_PRESETS | 15m, 1h, 2h, 4h, 12h, 24h |
| REFRESH_INTERVALS | Off, 15s, 30s, 60s |
| DEFAULT_REFRESH_MS | 60000 (60 seconds) |

#### Chart Colors (10 hex colors)

| Color Name | Hex Value |
|---|---|
| blue | `#3b82f6` |
| amber | `#f59e0b` |
| emerald | `#10b981` |
| red | `#ef4444` |
| violet | `#8b5cf6` |
| pink | `#ec4899` |
| cyan | `#06b6d4` |
| lime | `#84cc16` |
| orange | `#f97316` |
| indigo | `#6366f1` |

#### Formatters

| Function | Purpose | Example |
|---|---|---|
| `formatBytes(bytes)` | Human-readable bytes | 1.5 GB |
| `formatMs(ms)` | Milliseconds (omits 0) | 12.3ms |
| `formatAlwaysMs(ms)` | Milliseconds (always shows) | 0.0ms |
| `formatNumber(n)` | Locale-formatted number | 1,234,567 |
| `formatPercent(pct)` | Percentage with % | 45.2% |

#### Shared Classes

| Constant | Usage |
|---|---|
| `TAB_TRIGGER_CLASS` | Shared className for Radix TabsTrigger |

### 5.7 Custom Hooks (`lib/hooks/useTimeRange.ts` — 78 lines)

Provides time range management for pages that support preset and custom time ranges.

**Returns:**
- `timeRange` — Current time range state
- `setTimeRange` — Update time range
- `activePresetSeconds` — Currently active preset (0 if custom range)
- `rangeSec` — Total seconds in current range

---

## 6. Visual Design Patterns & Conventions

### 6.1 Card Pattern

Every content panel follows this consistent card design:

```html
<div className="bg-card border rounded-lg shadow-sm dark:ring-1 dark:ring-white/20">
  <div className="p-4">
    <!-- Card content -->
  </div>
</div>
```

**Key properties:**
- `bg-card` — Theme-aware background
- `border` — Subtle border
- `rounded-lg` — 8px border radius
- `shadow-sm` — Light shadow
- `dark:ring-1 dark:ring-white/20` — Additional ring for dark mode visibility

### 6.2 Button Pattern

```html
<button className="bg-primary text-primary-foreground rounded-md hover:bg-primary/90 px-4 py-2">
  Action
</button>
```

### 6.3 Status Badge Pattern

```html
<span className="badge-success px-2 py-1 rounded-sm text-xs font-medium">
  Up
</span>
```

Color mapping: success=green, danger=red, warning=amber, info=blue, neutral=gray

### 6.4 Horizontal Bar Chart Pattern

Custom bar chart implementation (not Recharts):

```
┌──────────────────────────────────────────┐
│ 1  Application Name    ████████░░  85%   │
│ 2  Another App         ██████░░░░  62%   │
│ 3  Third App           ████░░░░░░  41%   │
└──────────────────────────────────────────┘
```

Components:
- Rank number (bold, gray)
- Label text
- Colored progress bar (`bg-[color] h-2 rounded-full`)
- Value text (right-aligned)

### 6.5 Skeleton Loading Pattern

```html
<div className="animate-pulse space-y-3">
  <div className="h-4 bg-muted rounded w-3/4"></div>
  <div className="h-4 bg-muted rounded w-1/2"></div>
  <div className="h-4 bg-muted rounded w-2/3"></div>
</div>
```

### 6.6 Empty State Pattern

```html
<div className="text-center text-muted-foreground py-8">
  No data available
</div>
```

### 6.7 Table Pattern

```html
<div className="overflow-x-auto">
  <table className="w-full">
    <!-- Header + Body -->
  </table>
</div>
```

`overflow-x-auto` ensures horizontal scrolling on small screens.

### 6.8 Tab Pattern (Radix UI)

```html
<TabsList className="bg-muted/40">
  <TabsTrigger value="overview" className={TAB_TRIGGER_CLASS}>
    Overview
  </TabsTrigger>
</TabsList>
```

- Container: `bg-muted/40`
- Active: `bg-background shadow-sm`
- Inactive: gray text with hover

### 6.9 Site Selector Pattern

Used on most pages, includes:
- Dropdown/selector for DC, DRC, Office
- Optional colored badge per site (blue, emerald, amber)
- Drives SWR key changes for data refetching

### 6.10 Dark Mode Enhancements

In dark mode, cards receive an additional `ring-1 ring-white/20` to improve visibility against dark backgrounds. This is consistent across all card components.

---

## 7. Type System Overview

**File:** `src/types/index.ts` (398 lines)

### 7.1 API Envelope

```typescript
interface APIResponse<T> {
  data: T;
  // Standard API response wrapper
}
```

### 7.2 Auth Types

| Type | Fields | Description |
|---|---|---|
| `LoginRequest` | username, password | Login form payload |
| `TokenResponse` | access_token, refresh_token, user | Auth response |
| `User` | id, username, full_name, email, role, is_active, created_at | User object |

**Roles:** `superadmin | admin | operator | viewer`

### 7.3 Overview Types

| Type | Purpose |
|---|---|
| `ActiveUserKPI` | SSL/IPsec VPN user counts |
| `DeviceResourceStatus` | Device health (CPU, memory, sessions) |
| `TopApplication` | Application name + traffic volume |
| `WanLinkStatus` | WAN link up/down status |
| `SiteWanStatus` | Per-site WAN status collection |
| `ThroughputKPI` | Throughput metrics (in/out) |
| `OverviewData` | Complete overview page data |
| `HAStatusKPI` | HA cluster health |
| `WanInterfaceSummary` | Interface stats summary |
| `SiteWanBandwidth` | Per-site bandwidth data |
| `TopInboundService` | Top inbound VIP services |

### 7.4 Traffic Types

| Type | Version | Purpose |
|---|---|---|
| `TrafficSummary` | v1 | Summary statistics |
| `SankeyData` | v1 | Sankey diagram flow data |
| `RawFlowRecord` | v1 | Individual flow record |
| `TrafficFlowSummary` | v2.0 | Enhanced summary |
| `TrafficFlowChartData` | v2.0 | Time series chart data |
| `TrafficFlowTableData` | v2.0 | Table data with pagination |
| `TrafficFlowTableRecord` | v2.0 | Individual table row |

### 7.5 Interface Types

| Type | Purpose |
|---|---|
| `InterfaceTimelinePoint` | Single point in interface timeline |
| `InterfaceStatsItem` | Interface statistics entry |
| `InterfaceStatsData` | Complete interface statistics response |

### 7.6 HA Types

| Type | Purpose |
|---|---|
| `HAMember` | Individual HA member info |
| `HAStatusData` | Complete HA cluster status |

### 7.7 SD-WAN Types

| Type | Purpose |
|---|---|
| `LinkMetricPoint` | Single metric point per link |
| `SLATimeline` | SLA metric timeline |
| `LinkCurrentStatus` | Current link status |
| `SiteSLAStatus` | Per-site SLA status |
| `SLASummary` | Aggregated SLA summary |
| `SDWANData` | Complete SD-WAN data response |

### 7.8 Other Types

| Type | Purpose |
|---|---|
| `ResourceData` | Device resource metrics |
| `AlertRule` | Alert rule configuration |
| `Notification` | Notification item |
| `TimeRange` | Time range selection |
| `TIME_PRESETS` | Time preset definitions |
| `SankeyNode` | Sankey diagram node |
| `SankeyLink` | Sankey diagram link |
| `SankeyResponse` | Sankey API response |
| `TrafficInboundServiceItem` | Inbound service entry |
| `TrafficInboundSummary` | Inbound traffic summary |
| `TrafficInboundChartData` | Inbound chart data |
| `TrafficInboundTableData` | Inbound table data |
| `TrafficInternalSummary` | Internal traffic summary |
| `TrafficInternalChartData` | Internal chart data |

---

## 8. Known Issues & Technical Debt

### 8.1 Bug: Report Preview (Critical)

**Location:** `app/dashboard/reports/page.tsx`

The "Preview" action opens report HTML in a new tab using `window.open(url)` **without sending authentication headers**. This means:
- The request will fail if the server requires JWT auth
- User may see a raw HTML page or an error
- **Workaround:** Use the Download action instead

**Recommended Fix:** Use `apiFetch()` to get the HTML content, then open it in a new tab via `blob:` URL or inject into an iframe.

### 8.2 No Error Boundaries

The application lacks React Error Boundaries around page components. A runtime error in any page will crash the entire layout instead of showing a graceful error state for just that section.

### 8.3 No Loading Suspense Boundaries

While individual components handle their own loading states with skeletons, there are no `<Suspense>` boundaries for route-level loading. This means page transitions have no loading indicators at the route level.

### 8.4 Token Storage Security

JWT tokens are stored in `localStorage`, which is vulnerable to XSS attacks. A more secure approach would use `httpOnly` cookies. However, this is a common tradeoff in SPAs and is acceptable for internal network tools.

### 8.5 No Offline Support

The application has no service worker or offline caching. All data requires active network connectivity.

### 8.6 Limited Error Handling in SWR

SWR is configured with `shouldRetryOnError: false`, meaning API errors are not retried. This is intentional to avoid spamming failing endpoints, but it means users must manually refresh on transient errors.

### 8.7 Page Size Concerns

The Traffic Internet page is **1,140 lines** — the largest file in the project. This could benefit from component extraction to improve maintainability.

### 8.8 Missing Responsive Design Details

The sidebar collapses to 64px, but there's no explicit mobile breakpoint handling. The 240px sidebar may not be suitable for tablets or phones.

### 8.9 No Unit or Integration Tests

No test files are present in the frontend directory. There is no testing infrastructure (Jest, Vitest, Playwright, etc.).

### 8.10 No Storybook or Component Documentation

There is no Storybook setup or dedicated component documentation beyond this report.

### 8.11 Inconsistent Formatters

`formatMs` and `formatAlwaysMs` have overlapping functionality. `formatMs` omits the value when it's 0, while `formatAlwaysMs` always displays. This could be consolidated.

### 8.12 Emoji Icons in Navigation

The sidebar uses emoji characters for icons instead of a proper icon library. While functional, this may render differently across operating systems and may not be accessible to screen readers.

---

## 9. File Inventory

### 9.1 Complete File Listing

| # | File Path | Lines | Purpose |
|---|---|---|---|
| 1 | `src/app/page.tsx` | 5 | Root redirect to /dashboard/overview |
| 2 | `src/app/globals.css` | 113 | Design system, variables, print styles |
| 3 | `src/app/layout.tsx` | 25 | Root layout with ThemeProvider |
| 4 | `src/app/(auth)/login/page.tsx` | 111 | Login page |
| 5 | `src/app/dashboard/layout.tsx` | 287 | Dashboard shell (sidebar + header) |
| 6 | `src/app/dashboard/overview/page.tsx` | 530 | Overview KPI dashboard |
| 7 | `src/app/dashboard/traffic/page.tsx` | 1,140 | Internet traffic analysis |
| 8 | `src/app/dashboard/traffic-inbound/page.tsx` | 545 | Inbound VIP traffic |
| 9 | `src/app/dashboard/traffic-internal/page.tsx` | 367 | Internal traffic analysis |
| 10 | `src/app/dashboard/sdwan/page.tsx` | 552 | SD-WAN SLA monitoring |
| 11 | `src/app/dashboard/resources/page.tsx` | 793 | Device resources & bandwidth |
| 12 | `src/app/dashboard/reports/page.tsx` | 698 | Report generation |
| 13 | `src/app/dashboard/vpn/page.tsx` | — | VPN user monitoring |
| 14 | `src/app/dashboard/raw-data/page.tsx` | — | Raw flow records table |
| 15 | `src/app/dashboard/alerts/page.tsx` | — | Alert rules & history |
| 16 | `src/app/dashboard/users/page.tsx` | 368 | User management |
| 17 | `src/app/dashboard/settings/page.tsx` | 257 | User settings |
| 18 | `src/app/dashboard/activity-logs/page.tsx` | 170 | Audit trail |
| 19 | `src/components/charts/AreaChart.tsx` | 165 | Recharts wrapper component |
| 20 | `src/components/panels/TimeRangePicker.tsx` | 183 | Date/time range picker |
| 21 | `src/components/ThemeProvider.tsx` | 28 | next-themes wrapper |
| 22 | `src/components/ThemeToggle.tsx` | 46 | Theme toggle button |
| 23 | `src/lib/api.ts` | 171 | API layer with JWT auth |
| 24 | `src/lib/constants.ts` | 95 | Constants, formatters, chart colors |
| 25 | `src/lib/utils.ts` | 22 | Utility functions |
| 26 | `src/lib/hooks/useTimeRange.ts` | 78 | Time range hook |
| 27 | `src/types/index.ts` | 398 | TypeScript type definitions |

### 9.2 Line Count Summary

| Category | Files | Lines |
|---|---|---|
| App Pages (routes) | 15 | ~5,101 |
| Components | 4 | 422 |
| Library/Utils | 4 | 366 |
| Types | 1 | 398 |
| **Total** | **26** | **~6,800+** |

### 9.3 Largest Files

1. `traffic/page.tsx` — 1,140 lines
2. `resources/page.tsx` — 793 lines
3. `reports/page.tsx` — 698 lines
4. `sdwan/page.tsx` — 552 lines
5. `traffic-inbound/page.tsx` — 545 lines
6. `overview/page.tsx` — 530 lines

---

*This report provides a complete architectural overview of the NOD frontend. For questions or updates, refer to the source files listed in Section 9.*
