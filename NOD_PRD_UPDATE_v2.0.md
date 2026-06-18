# NOD — Network Operations Dashboard
## PRD Update v2.0 + Spesifikasi Teknis Implementasi

| Metadata | Value |
|---|---|
| **Dokumen** | `NOD_PRD_UPDATE_v2.0.md` |
| **Tanggal** | 12 Juni 2026 |
| **Status** | DRAFT — Siap untuk Review |
| **Stack** | Next.js 14+ / TypeScript · FastAPI · PostgreSQL · OpenSearch · Recharts |
| **Stakeholder** | Network Engineering, NOC Team, Product Management |

---

## ⚠️ GLOBAL CONSTRAINT — HARD RULE

> **DILARANG KERAS** melakukan perubahan pada:
> **main layout, sidebar, header, footer, dan struktur navigasi global** yang sudah ada.
>
> Perubahan dalam dokumen ini **hanya berlaku** pada komponen, visualisasi,
> dan query data di modul-modul yang secara eksplisit disebutkan.
> Konsistensi desain dengan existing dashboard **wajib** dipertahankan.

---

## Daftar Isi

1. [Latar Belakang](#1-latar-belakang)
2. [Scope & Constraints](#2-scope--constraints)
3. [User Stories](#3-user-stories)
4. [Acceptance Criteria](#4-acceptance-criteria)
5. [Spesifikasi Teknis Frontend](#5-spesifikasi-teknis-frontend)
6. [Spesifikasi Teknis Backend / Query OpenSearch](#6-spesifikasi-teknis-backend--query-opensearch)
7. [Interface Mapping Reference](#7-interface-mapping-reference)
8. [Non-Functional Requirements](#8-non-functional-requirements)
9. [Definition of Done](#9-definition-of-done)
10. [Appendix A — Semantic Field Reference](#appendix-a--semantic-field-reference)

---

## 1. Latar Belakang

### 1.1 Konteks Sistem

Network Operations Dashboard (NOD) adalah platform observability terpusat yang digunakan tim NOC untuk memonitor infrastruktur jaringan FortiGate secara real-time. Dashboard dibangun di atas stack:

| Layer | Teknologi |
|---|---|
| Data Collection | FortiGate IPFIX/NetFlow + Telegraf (SNMP) |
| Storage | OpenSearch — indices: `flow-*`, `telegraf-index-*` |
| Backend | FastAPI (Python) — query proxy, normalisasi, caching |
| Frontend | Next.js 14+ / React / TypeScript |
| Charting | Recharts |

### 1.2 Motivasi Update v2.0

Update ini diperlukan untuk menyelesaikan lima gap yang ada pada versi sebelumnya:

1. **Cakupan monitoring belum eksplisit** — 3 site FortiGate harus dimonitor secara konsisten dan terstruktur.
2. **Traffic Flow kurang granular** — Belum ada stacked bar chart per-aplikasi bergaya Elastiflow yang mendukung analisis throughput.
3. **Interface Stats mengandung noise** — Interface irrelevant (loopback, internal, virtual) muncul bersama interface produksi.
4. **HA Status tidak tervisualisasi** — Tidak ada indikator eksplisit untuk status High Availability di site DC.
5. **SD-WAN SLA rentan silent failure** — Tidak ada validasi bahwa data ter-fetch dan ter-render dengan benar per site.

### 1.3 Arsitektur Data Flow

```
FortiGate (IPFIX/NetFlow)
    │
    ▼
Flow Collector (nfcapd/logstash)
    │
    ▼
OpenSearch [flow-*]          ◄── flow.export.ip.addr = site filter
    │
    ▼
FastAPI Backend (query proxy)
    │
    ▼
Next.js Frontend (React/Recharts)

FortiGate (SNMP via Telegraf)
    │
    ▼
OpenSearch [telegraf-index-*] ◄── tag.source = site filter
```

---

## 2. Scope & Constraints

### 2.1 Site yang Dimonitor

| ID Site | Hostname | Source IP (IPFIX / flow-*) | Source IP (Telegraf / telegraf-index-*) |
|---|---|---|---|
| `Site_FGT-DC` | FGT-DC | `10.80.150.1` | `10.80.150.1` |
| `Site_FGT-DRC` | FGT-DRC | `10.90.150.1` | `10.90.150.1` |
| `Site_FGT-Office` | FGT-Office | `10.10.10.10` | `10.10.10.10` |

### 2.2 Modul per Site

Setiap site wajib memiliki 5 modul berikut:

| No | Modul | Update Status | Catatan |
|---|---|---|---|
| 1 | Traffic Flow | **Diperbarui** | 4 bagian visual baru |
| 2 | SD-WAN SLA | **Diperbarui** | Tambah validasi fetch/render eksplisit |
| 3 | Interface Stats | **Diperbarui** | Filter ketat per `ifIndex` mapping |
| 4 | FortiGate Resource Stats | **Diperbarui** | Tambah HA Status panel di DC |
| 5 | VPN Sessions | **Tidak ada perubahan** | Tetap seperti existing |

### 2.3 Out of Scope

- Global layout: main layout, sidebar, header, footer, routing
- Penambahan site di luar 3 site yang tertulis
- Perubahan modul VPN Sessions
- Redesign halaman login atau user management
- Perubahan pipeline data collection (IPFIX parser, Telegraf config, OpenSearch mappings)
- Perubahan indeks OpenSearch yang sudah ada

---

## 3. User Stories

### Epic 1 — Traffic Flow (Modul Diperbarui)

---

#### US-TF-01 — Traffic Flow Summary

> **Sebagai** NOC Operator,
> **Saya ingin** melihat ringkasan traffic dalam bentuk widget/metric untuk setiap site,
> **Sehingga** saya dapat dengan cepat mengidentifikasi aplikasi, AS, klien, dan server
> yang paling dominan dalam periode waktu tertentu.

**Widget yang diperlukan (8 widget):**

| # | Widget | Visualisasi |
|---|---|---|
| 1 | Top Application | Bar chart / ranked list |
| 2 | Application Categories | Donut chart / ranked list |
| 3 | Top Destination AS Organization | Bar chart / ranked list |
| 4 | Top Destination AS Country | Bar chart + flag |
| 5 | Top Client IPs | Ranked list |
| 6 | Top Server IPs | Ranked list |
| 7 | Protocol Distribution | Pie/Donut chart |
| 8 | Egress Interface Breakdown | Bar chart |

---

#### US-TF-02 — Traffic Details: Stacked Bar Chart

> **Sebagai** NOC Operator atau Network Analyst,
> **Saya ingin** melihat breakdown traffic per aplikasi dalam stacked bar chart per 60 detik
> bergaya Elastiflow "Conversations",
> **Sehingga** saya dapat menganalisis pola throughput aplikasi sepanjang waktu secara visual
> dan mengidentifikasi aplikasi dominan pada waktu tertentu.

**Detail fungsional:**

- Setiap bar = 1 interval 60 detik
- Setiap segmen warna dalam bar = 1 Application Name
- **Legend Kiri** — Speed Usage Mbps (untuk seluruh time range):
  ```
  speedMbps[app] = (totalBytes[app] × 8) / rangeDurationSeconds / 1_000_000
  ```
- **Legend Kanan** — Daftar Application Name unik yang aktif dalam time range
- **Hover Tooltip** — Menampilkan speed per aplikasi **pada timestamp spesifik** bar yang di-hover:
  ```
  bucketSpeedMbps[app] = (bytesInBucket[app] × 8) / 60 / 1_000_000
  ```
- Range waktu mengikuti time picker yang dipilih user

---

#### US-TF-03 — Traffic Details: Data Table

> **Sebagai** NOC Operator,
> **Saya ingin** melihat detail per-flow di bawah stacked bar chart,
> **Sehingga** saya dapat menginvestigasi komunikasi spesifik antara client dan server.

**Kolom wajib:** `Client IP` | `Server IP` | `Service / Application Name` | `Bytes` | `Packets` | `Sessions`

---

#### US-TF-04 — Sankey Diagram (No Change)

> **Sebagai** NOC Operator,
> **Saya ingin** tetap melihat Sankey diagram dengan alur: Zone → Top Apps → Egress → AS Country,
> **Sehingga** saya dapat memahami jalur traffic secara end-to-end.

*Tidak ada perubahan fungsional. Verifikasi bahwa Sankey existing tetap berfungsi setelah update komponen lain.*

---

### Epic 2 — SD-WAN SLA

---

#### US-SDWAN-01 — Validasi Render per Site

> **Sebagai** NOC Operator,
> **Saya ingin** memastikan panel Performance SLA ter-render dengan benar untuk ketiga site,
> **Sehingga** saya tidak kehilangan data SLA akibat query failure atau empty state
> yang tidak terdeteksi.

---

### Epic 3 — Interface Stats

---

#### US-IFACE-01 — Filtered Interface Display

> **Sebagai** NOC Operator,
> **Saya ingin** hanya melihat interface yang relevan sesuai mapping `ifIndex` per site,
> **Sehingga** tampilan tidak terkontaminasi oleh interface loopback, internal,
> atau virtual yang tidak relevan untuk monitoring.

---

### Epic 4 — FortiGate Resource Stats

---

#### US-FGTRES-01 — FortiGate HA Status (DC Only)

> **Sebagai** Network Engineer,
> **Saya ingin** melihat status High Availability FortiGate DC secara eksplisit
> di halaman Resource Stats,
> **Sehingga** saya dapat mendeteksi failover atau degradasi cluster HA
> sesegera mungkin tanpa harus masuk ke GUI FortiGate.

---

## 4. Acceptance Criteria

### AC-TF-01 — Traffic Flow Summary Widgets

| # | Kriteria | Verifikasi |
|---|---|---|
| AC-TF-01.1 | Semua 8 widget ter-render tanpa error untuk semua 3 site | Unit test + visual QA |
| AC-TF-01.2 | Data di-filter berdasarkan `flow.export.ip.addr` sesuai site aktif | Query log check |
| AC-TF-01.3 | Data di-filter sesuai rentang waktu dari time picker | E2E test |
| AC-TF-01.4 | Jika data kosong, tampilkan empty state yang jelas (bukan blank/error) | Edge case test |

---

### AC-TF-02 — Stacked Bar Chart

| # | Kriteria | Verifikasi |
|---|---|---|
| AC-TF-02.1 | Interval histogram `fixed_interval: "60s"` | Query assertion |
| AC-TF-02.2 | Setiap Application Name memiliki warna konsisten dan berbeda | Visual QA |
| AC-TF-02.3 | Legend kiri: formula `(totalBytes × 8) / rangeSecs / 1_000_000` Mbps | Unit test kalkulasi |
| AC-TF-02.4 | Legend kanan: daftar Application Name unik dalam range | Data assertion |
| AC-TF-02.5 | Hover tooltip menampilkan speed + app list khusus timestamp bar tersebut | Interaction test |
| AC-TF-02.6 | Tooltip formula per bucket: `(bytesInBucket × 8) / 60 / 1_000_000` Mbps | Unit test kalkulasi |
| AC-TF-02.7 | X-axis menampilkan label timestamp `HH:MM:SS` | Visual QA |
| AC-TF-02.8 | Y-axis menampilkan skala Mbps | Visual QA |
| AC-TF-02.9 | Max 20 top apps ditampilkan; sisa bytes dikelompokkan sebagai `"Others"` | Data normalization test |

---

### AC-TF-03 — Traffic Details Data Table

| # | Kriteria | Verifikasi |
|---|---|---|
| AC-TF-03.1 | 6 kolom wajib: Client IP, Server IP, App Name, Bytes, Packets, Sessions | Visual QA |
| AC-TF-03.2 | Default sort: Bytes descending | Behavior test |
| AC-TF-03.3 | Mendukung pagination atau virtual scroll untuk > 100 baris | Performance test |
| AC-TF-03.4 | Bytes ditampilkan human-readable: KB / MB / GB | Unit test formatter |

---

### AC-SDWAN-01 — SD-WAN SLA Validation

| # | Kriteria | Verifikasi |
|---|---|---|
| AC-SDWAN-01.1 | Komponen mendeteksi dan menampilkan error state jika query OpenSearch gagal | Mock error test |
| AC-SDWAN-01.2 | Komponen menampilkan warning `"No SLA data"` jika `hits.total.value === 0` | Empty data test |
| AC-SDWAN-01.3 | Loading spinner aktif selama fetch berlangsung | Behavior test |
| AC-SDWAN-01.4 | Setiap panel SLA secara eksplisit melabeli site (DC / DRC / Office) | Visual QA |

---

### AC-IFACE-01 — Interface Stats Filtered

| # | Kriteria | Verifikasi |
|---|---|---|
| AC-IFACE-01.1 | Hanya interface sesuai mapping `ifIndex` yang ditampilkan (max 5 per site) | Data assertion |
| AC-IFACE-01.2 | Filter query: kombinasi `tag.source` AND `tag.ifIndex` (terms filter) | Query log |
| AC-IFACE-01.3 | Header panel menampilkan `tag.ifName` bukan raw `ifIndex` | Visual QA |
| AC-IFACE-01.4 | Throughput dihitung dari delta counter: `(cur - prev) × 8 / 60 / 1_000_000` Mbps | Unit test kalkulasi |
| AC-IFACE-01.5 | `ifOperStatus = 1` → badge hijau "UP"; nilai lain → badge merah/kuning "DOWN" | Visual QA |
| AC-IFACE-01.6 | Site FGT-Office menampilkan 4 interface; interface ke-5 hanya jika data tersedia | Data assertion |

---

### AC-FGTRES-01 — FortiGate HA Status

| # | Kriteria | Verifikasi |
|---|---|---|
| AC-FGTRES-01.1 | Panel HA **hanya muncul** di `siteId === 'DC'` | Conditional render test |
| AC-FGTRES-01.2 | Panel menampilkan: HA Role, Member Count, Sync Status | Visual QA |
| AC-FGTRES-01.3 | Badge hijau = healthy, kuning = degraded, merah = critical | Visual QA |
| AC-FGTRES-01.4 | Data di-refresh sesuai interval polling dashboard | Refresh interval test |

---

## 5. Spesifikasi Teknis Frontend

> Semua komponen berikut adalah **tambahan atau modifikasi** pada modul yang ada.
> **Tidak ada perubahan** pada komponen layout, navbar, sidebar, footer, atau routing.

---

### 5.1 Traffic Flow Summary — Widget Components

```typescript
// components/traffic-flow/SummaryPanel.tsx

interface SummaryPanelProps {
  siteId:    'DC' | 'DRC' | 'Office';
  sourceIp:  string;       // flow.export.ip.addr filter value
  timeRange: TimeRange;    // { from: string; to: string } — ISO 8601
}

// Delapan sub-komponen yang di-mount di dalam SummaryPanel:
// <TopApplicationsWidget />            — Bar chart, top 10 by bytes
// <AppCategoriesWidget />              — Donut chart, top categories
// <TopDestinationASOrganizationWidget /> — Ranked list
// <TopDestinationASCountryWidget />    — Ranked list + flag icon
// <TopClientIPsWidget />               — Ranked list
// <TopServerIPsWidget />               — Ranked list
// <ProtocolDistributionWidget />       — Pie chart (Recharts <PieChart>)
// <EgressInterfaceBreakdownWidget />   — Bar chart (Recharts <BarChart>)
```

Setiap sub-komponen menerima `{ siteId, sourceIp, timeRange }` dan mengelola
state fetch-nya sendiri (isolated loading, error, empty state).

---

### 5.2 Traffic Details — Stacked Bar Chart (Recharts)

#### Types & Interfaces

```typescript
// types/trafficDetails.ts

interface TimeRange {
  from: string;    // ISO 8601
  to:   string;   // ISO 8601
  durationSeconds: number;  // (new Date(to) - new Date(from)) / 1000
}

// Satu bucket = interval 60 detik
// Key adalah nama aplikasi; value adalah total bytes dalam bucket tersebut
interface TimeSeriesBucket {
  timestamp:   string;          // "HH:MM:SS" — label X-axis
  timestampMs: number;          // Unix ms — untuk sorting
  [appName: string]: number | string;
}

interface AppSpeedEntry {
  appName:   string;
  speedMbps: number;
}
```

#### Custom Hook

```typescript
// hooks/useTrafficDetailsChart.ts

interface TrafficDetailsChartState {
  chartData:        TimeSeriesBucket[];
  appNames:         string[];                        // deduplicated, sorted by total bytes desc
  globalSpeedByApp: Record<string, number>;          // Mbps per app — seluruh time range
  hoveredBucket:    TimeSeriesBucket | null;
  isLoading:        boolean;
  error:            string | null;
}

// ── Speed Calculation Formulas ──────────────────────────────────────────────
//
// Global Speed (Legend Kiri — seluruh time range):
//   globalSpeedMbps[app] = (totalBytes[app] × 8) / rangeDurationSeconds / 1_000_000
//
// Per-Bucket Speed (Tooltip hover pada bar spesifik):
//   bucketSpeedMbps[app] = (bytesInBucket[app] × 8) / 60 / 1_000_000
//
// ─────────────────────────────────────────────────────────────────────────────

export const useTrafficDetailsChart = ({
  siteId, sourceIp, timeRange
}: TrafficDetailsChartProps): TrafficDetailsChartState => {
  // 1. Fetch raw aggregation dari API (POST /api/traffic-details/chart)
  // 2. Normalize: bucket per 60s, top-20 apps, sisa → "Others"
  // 3. Hitung globalSpeedByApp menggunakan timeRange.durationSeconds
  // 4. Return state
};
```

#### Komponen Utama

```typescript
// components/traffic-flow/TrafficDetailsChart.tsx

import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer
} from 'recharts';

// Palet 20 warna distinktif — konsisten dengan gaya Elastiflow
const APP_COLOR_PALETTE = [
  '#4E79A7', '#F28E2B', '#E15759', '#76B7B2', '#59A14F',
  '#EDC948', '#B07AA1', '#FF9DA7', '#9C755F', '#BAB0AC',
  '#86BCB6', '#FF9D9A', '#D7B5A6', '#79706E', '#D4A6C8',
  '#FABFD2', '#B6992D', '#499894', '#E15759', '#4E79A7',
];

// ── Custom Tooltip ─────────────────────────────────────────────────────────

const TrafficDetailsTooltip: React.FC<TooltipProps<number, string>> = ({
  active, payload, label
}) => {
  if (!active || !payload?.length) return null;

  const bucketApps: AppSpeedEntry[] = payload
    .map(entry => ({
      appName:   entry.dataKey as string,
      speedMbps: ((entry.value as number) * 8) / 60 / 1_000_000,
    }))
    .filter(e => e.speedMbps > 0)
    .sort((a, b) => b.speedMbps - a.speedMbps);

  return (
    <div className="nod-tooltip">
      <p className="nod-tooltip__timestamp">{label}</p>
      <div className="nod-tooltip__rows">
        {bucketApps.map(({ appName, speedMbps }) => (
          <div key={appName} className="nod-tooltip__row">
            <span className="nod-tooltip__app">{appName}</span>
            <span className="nod-tooltip__speed">
              {speedMbps >= 1
                ? `${speedMbps.toFixed(2)} Mbps`
                : `${(speedMbps * 1000).toFixed(0)} Kbps`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ── Legend Kiri: Speed per App (entire range) ──────────────────────────────

const LeftSpeedLegend: React.FC<{
  globalSpeedByApp: Record<string, number>;
  appColors:        Record<string, string>;
}> = ({ globalSpeedByApp, appColors }) => (
  <div className="nod-legend nod-legend--left">
    <p className="nod-legend__title">Speed Usage</p>
    {Object.entries(globalSpeedByApp)
      .sort(([, a], [, b]) => b - a)
      .map(([appName, speedMbps]) => (
        <div key={appName} className="nod-legend__row">
          <span
            className="nod-legend__color-dot"
            style={{ backgroundColor: appColors[appName] }}
          />
          <span className="nod-legend__speed">
            {speedMbps >= 1
              ? `${speedMbps.toFixed(2)} Mbps`
              : `${(speedMbps * 1000).toFixed(0)} Kbps`}
          </span>
        </div>
      ))}
  </div>
);

// ── Legend Kanan: App Name List ────────────────────────────────────────────

const RightAppNameLegend: React.FC<{
  appNames:  string[];
  appColors: Record<string, string>;
}> = ({ appNames, appColors }) => (
  <div className="nod-legend nod-legend--right">
    <p className="nod-legend__title">Applications</p>
    {appNames.map(appName => (
      <div key={appName} className="nod-legend__row">
        <span
          className="nod-legend__color-dot"
          style={{ backgroundColor: appColors[appName] }}
        />
        <span className="nod-legend__app-name">{appName}</span>
      </div>
    ))}
  </div>
);

// ── Main Chart Component ───────────────────────────────────────────────────

export const TrafficDetailsChart: React.FC<TrafficDetailsChartProps> = ({
  siteId, sourceIp, timeRange
}) => {
  const {
    chartData, appNames, globalSpeedByApp, isLoading, error
  } = useTrafficDetailsChart({ siteId, sourceIp, timeRange });

  // Build stable color mapping — recompute only when appNames changes
  const appColors = useMemo(
    () => Object.fromEntries(
      appNames.map((name, i) => [name, APP_COLOR_PALETTE[i % APP_COLOR_PALETTE.length]])
    ),
    [appNames]
  );

  if (isLoading) return <LoadingSpinner />;
  if (error)     return <ErrorBanner message={error} />;
  if (!chartData.length) return <EmptyState message="No traffic data in selected range" />;

  return (
    <div className="nod-traffic-details">
      <div className="nod-traffic-details__chart-row">

        {/* Legend Kiri */}
        <LeftSpeedLegend
          globalSpeedByApp={globalSpeedByApp}
          appColors={appColors}
        />

        {/* Stacked Bar Chart */}
        <ResponsiveContainer width="100%" height={400}>
          <BarChart
            data={chartData}
            margin={{ top: 10, right: 20, left: 20, bottom: 10 }}
          >
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="timestamp"
              tick={{ fontSize: 11 }}
            />
            <YAxis
              tickFormatter={val =>
                `${(val * 8 / 60 / 1_000_000).toFixed(1)} Mbps`
              }
            />
            <Tooltip content={<TrafficDetailsTooltip />} />
            {appNames.map(appName => (
              <Bar
                key={appName}
                dataKey={appName}
                stackId="traffic"
                fill={appColors[appName]}
                isAnimationActive={false}  // Penting: hindari lag pada range panjang
              />
            ))}
          </BarChart>
        </ResponsiveContainer>

        {/* Legend Kanan */}
        <RightAppNameLegend appNames={appNames} appColors={appColors} />
      </div>
    </div>
  );
};
```

> **Catatan Performa:**
> - `isAnimationActive={false}` wajib dipasang pada setiap `<Bar>` untuk menghindari
>   re-animation lag ketika time range panjang (> 4 jam = 240+ bar).
> - `useMemo` pada `appColors` mencegah re-compute warna di setiap render cycle.
> - Batasi top-20 apps di normalization layer sebelum masuk ke chart state;
>   bytes sisanya dikelompokkan sebagai `"Others"`.

---

### 5.3 Traffic Details — Data Table

```typescript
// components/traffic-flow/TrafficDetailsTable.tsx

interface FlowRecord {
  clientIp: string;
  serverIp: string;
  appName:  string;
  bytes:    number;
  packets:  number;
  sessions: number;
}

// ── Bytes Formatter ────────────────────────────────────────────────────────
const formatBytes = (bytes: number): string => {
  if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(2)} GB`;
  if (bytes >= 1_048_576)     return `${(bytes / 1_048_576).toFixed(2)} MB`;
  if (bytes >= 1_024)         return `${(bytes / 1_024).toFixed(2)} KB`;
  return `${bytes} B`;
};

// ── TanStack Table Column Definitions ─────────────────────────────────────
// (Gunakan react-virtualized atau TanStack Virtual untuk > 100 baris)
const columns: ColumnDef<FlowRecord>[] = [
  { accessorKey: 'clientIp', header: 'Client IP',          size: 140 },
  { accessorKey: 'serverIp', header: 'Server IP',          size: 140 },
  { accessorKey: 'appName',  header: 'Service / App Name', size: 200 },
  {
    accessorKey: 'bytes',
    header: 'Bytes',
    size: 120,
    cell:       ({ getValue }) => formatBytes(getValue<number>()),
    sortingFn:  'basic',
  },
  { accessorKey: 'packets',  header: 'Packets',  size: 100 },
  { accessorKey: 'sessions', header: 'Sessions', size: 90  },
];
// Default sort state: [{ id: 'bytes', desc: true }]
```

---

### 5.4 SD-WAN SLA — Validation Layer

Tambahkan `useSdwanSlaData` hook yang membungkus logic fetch existing dengan
validasi state eksplisit:

```typescript
// hooks/useSdwanSlaData.ts

type FetchStatus = 'idle' | 'loading' | 'success' | 'empty' | 'error';

interface SdwanSlaState {
  data:          SdwanSlaRecord[];
  status:        FetchStatus;
  errorMessage:  string | null;
  lastUpdated:   Date | null;
}

// ── Render Logic di Komponen Parent ───────────────────────────────────────
// switch (status) {
//   case 'loading':  return <LoadingSpinner />;
//   case 'empty':    return <EmptyState message="No SLA data for this site" />;
//   case 'error':    return <ErrorBanner message={errorMessage} onRetry={refetch} />;
//   case 'success':  return <ExistingSlaPanel data={data} />;
// }

// ── Site Label Component (wajib ditampilkan) ───────────────────────────────
// <SiteLabel site="DC" />
// <SiteLabel site="DRC" />
// <SiteLabel site="Office" />
```

---

### 5.5 Interface Stats — Filtered Components

#### Constants: Interface Mapping

```typescript
// constants/interfaceMapping.ts

export const INTERFACE_MAPPING = {
  DC: {
    sourceIp:   '10.80.150.1',
    interfaces: [
      { ifIndex: '39', label: 'MPLS LinkNet'   },
      { ifIndex: '38', label: 'MPLS iForte'    },
      { ifIndex: '3',  label: 'WAN LinkNet'    },
      { ifIndex: '4',  label: 'WAN iForte'     },
      { ifIndex: '18', label: 'MPLS Dukcapil'  },
    ],
  },
  DRC: {
    sourceIp:   '10.90.150.1',
    interfaces: [
      { ifIndex: '39', label: 'MPLS LinkNet'   },
      { ifIndex: '38', label: 'MPLS iForte'    },
      { ifIndex: '7',  label: 'WAN LinkNet'    },
      { ifIndex: '8',  label: 'WAN iForte'     },
      { ifIndex: '18', label: 'MPLS Dukcapil'  },
    ],
  },
  Office: {
    sourceIp:   '10.10.10.10',
    interfaces: [
      { ifIndex: '14', label: 'MPLS LinkNet'   },
      { ifIndex: '15', label: 'MPLS iForte'    },
      { ifIndex: '16', label: 'WAN LDP'        },
      { ifIndex: '17', label: 'WAN iForte'     },
      // Interface ke-5: dinamis dari data, atau tidak ditampilkan jika null
    ],
  },
} as const;
```

#### Interface Stat Card Component

```typescript
// components/interface-stats/InterfaceStatCard.tsx

interface InterfaceStatCardProps {
  siteId:    'DC' | 'DRC' | 'Office';
  ifIndex:   string;
  label:     string;     // Display name: "MPLS LinkNet", "WAN iForte", dll.
  sourceIp:  string;
  timeRange: TimeRange;
}

// Setiap card menampilkan:
// ┌─────────────────────────────────────────────────┐
// │  MPLS LinkNet          ● UP   2000 Mbps nominal │
// │  [Area Chart: inbound (biru) / outbound (oranye) Mbps] │
// │  In: 142.3 Mbps   Out: 87.1 Mbps               │
// └─────────────────────────────────────────────────┘

// Throughput Calculation (dari delta counter kumulatif):
// throughputMbps = ((maxOctets_current - maxOctets_prev) × 8) / 60 / 1_000_000
//
// Counter reset guard (jika delta negatif → tampilkan null/skip):
// if (delta < 0) return null;  // counter reset atau device reboot
```

---

### 5.6 FortiGate HA Status — DC Only

```typescript
// components/fortigate-resource/HAStatusPanel.tsx

// ── Conditional Mount (di parent FortiGateResourcePage) ───────────────────
// {siteId === 'DC' && (
//   <HAStatusPanel sourceIp="10.80.150.1" refreshInterval={30_000} />
// )}

interface HAMember {
  memberIndex: number;
  role:        'primary' | 'secondary';
  syncStatus:  'in-sync' | 'out-of-sync' | 'unknown';
  priority:    number;
  hostname?:   string;
}

interface HAStatusState {
  haMode:        'active-passive' | 'active-active' | 'standalone';
  members:       HAMember[];
  overallHealth: 'healthy' | 'degraded' | 'critical';
  lastUpdated:   Date | null;
}

// ── Health Assessment Logic ────────────────────────────────────────────────
// healthy  → haMode !== 'standalone' AND all members syncStatus === 'in-sync'
// degraded → haMode !== 'standalone' AND any member syncStatus !== 'in-sync'
// critical → haMode === 'standalone' ATAU member count < 2

// ── Badge Colors ──────────────────────────────────────────────────────────
// healthy  → green  "HA: ACTIVE — 2 Members In-Sync"
// degraded → yellow "HA: DEGRADED — Member Out-of-Sync"
// critical → red    "HA: CRITICAL — Standalone / Member Down"
```

---

## 6. Spesifikasi Teknis Backend / Query OpenSearch

> **Konvensi:**
> - `<FROM>` / `<TO>` = nilai ISO 8601 dari time picker
> - `<SOURCE_IP>` = IP eksportir / source sesuai site yang aktif
> - Semua query menggunakan `"size": 0` (aggregation-only, tidak perlu hits)

---

### 6.1 Traffic Details — Stacked Bar Chart Query

**Index:** `flow-*`
**Endpoint:** `POST /flow-*/_search`

```json
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "range": {
            "@timestamp": {
              "gte": "<FROM>",
              "lte": "<TO>"
            }
          }
        },
        {
          "term": {
            "flow.export.ip.addr": "<SOURCE_IP>"
          }
        }
      ]
    }
  },
  "aggs": {
    "per_minute_bucket": {
      "date_histogram": {
        "field":          "@timestamp",
        "fixed_interval": "60s",
        "extended_bounds": {
          "min": "<FROM>",
          "max": "<TO>"
        },
        "min_doc_count": 0
      },
      "aggs": {
        "top_apps": {
          "terms": {
            "field":   "flow.app.name",
            "size":    20,
            "order":   { "total_bytes": "desc" },
            "missing": "Unknown"
          },
          "aggs": {
            "total_bytes": {
              "sum": { "field": "flow.bytes" }
            }
          }
        },
        "others_bytes": {
          "sum_bucket": {
            "buckets_path": "top_apps>total_bytes"
          }
        }
      }
    }
  }
}
```

**Response Normalization (API Layer):**

```python
# fastapi/routers/traffic_details.py (pseudocode)

def normalize_chart_response(raw_aggs, time_range_seconds):
    chart_data = []
    all_app_bytes_total = defaultdict(float)

    for bucket in raw_aggs["per_minute_bucket"]["buckets"]:
        ts = bucket["key_as_string"]    # ISO timestamp
        row = {"timestamp": ts[:19].replace("T", " ")}  # "YYYY-MM-DD HH:MM:SS"

        for app_bucket in bucket["top_apps"]["buckets"]:
            app_name  = app_bucket["key"]
            app_bytes = app_bucket["total_bytes"]["value"]
            row[app_name] = app_bytes
            all_app_bytes_total[app_name] += app_bytes

        chart_data.append(row)

    # Global speed per app (Legend Kiri)
    global_speed_by_app = {
        app: (total_bytes * 8) / time_range_seconds / 1_000_000
        for app, total_bytes in all_app_bytes_total.items()
    }

    # App names sorted by total bytes desc
    app_names = sorted(all_app_bytes_total, key=all_app_bytes_total.get, reverse=True)

    return {
        "chartData":        chart_data,
        "appNames":         app_names,
        "globalSpeedByApp": global_speed_by_app,
    }
```

---

### 6.2 Traffic Details — Data Table Query

**Index:** `flow-*`

```json
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "range": {
            "@timestamp": { "gte": "<FROM>", "lte": "<TO>" }
          }
        },
        {
          "term": {
            "flow.export.ip.addr": "<SOURCE_IP>"
          }
        }
      ]
    }
  },
  "aggs": {
    "flow_table": {
      "composite": {
        "size": 500,
        "sources": [
          {
            "client_ip": {
              "terms": { "field": "flow.client.ip" }
            }
          },
          {
            "server_ip": {
              "terms": { "field": "flow.server.ip" }
            }
          },
          {
            "app_name": {
              "terms": {
                "field":          "flow.app.name",
                "missing_bucket": true
              }
            }
          }
        ]
      },
      "aggs": {
        "total_bytes":   { "sum":         { "field": "flow.bytes"   } },
        "total_packets": { "sum":         { "field": "flow.packets" } },
        "session_count": { "value_count": { "field": "flow.id"      } }
      }
    }
  }
}
```

**Paginasi** menggunakan `after` parameter dari `flow_table.after_key`:

```json
{
  "aggs": {
    "flow_table": {
      "composite": {
        "size": 500,
        "after": { "client_ip": "...", "server_ip": "...", "app_name": "..." },
        "sources": [ ... ]
      }
    }
  }
}
```

> **Catatan:** Jika field `flow.id` tidak tersedia di index, gunakan `"doc_count"` dari
> bucket composite sebagai nilai `sessions`.

---

### 6.3 Interface Stats — Per-Site Filtered Query

> **Prinsip Kalkulasi Throughput:**
> `ifHCInOctets` dan `ifHCOutOctets` adalah **counter kumulatif** (monotonically increasing).
> Throughput aktual = delta counter antar interval:
>
> ```
> throughputMbps = (max_current_bucket - max_prev_bucket) × 8 / 60 / 1_000_000
> ```
>
> Jika delta negatif (counter reset / device reboot): abaikan atau tandai sebagai `null`.
> Gunakan `derivative` pipeline aggregation OpenSearch atau hitung delta di API layer.

**Index:** `telegraf-index-*`

#### 6.3.1 Site_FGT-DC

```json
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "range": {
            "@timestamp": { "gte": "<FROM>", "lte": "<TO>" }
          }
        },
        { "term": { "measurement_name": "fgt_iface_stats" } },
        { "term": { "tag.source":       "10.80.150.1"     } },
        {
          "terms": {
            "tag.ifIndex": ["39", "38", "3", "4", "18"]
          }
        }
      ]
    }
  },
  "aggs": {
    "by_interface": {
      "terms": {
        "field": "tag.ifIndex",
        "size":  5
      },
      "aggs": {
        "interface_name": {
          "terms": { "field": "tag.ifName", "size": 1 }
        },
        "by_time": {
          "date_histogram": {
            "field":          "@timestamp",
            "fixed_interval": "60s"
          },
          "aggs": {
            "max_in_octets":  { "max": { "field": "fgt_iface_stats.ifHCInOctets"    } },
            "max_out_octets": { "max": { "field": "fgt_iface_stats.ifHCOutOctets"   } },
            "speed_mbps":     { "max": { "field": "fgt_iface_stats.ifHighSpeed_Mbps"} },
            "oper_status":    { "max": { "field": "fgt_iface_stats.ifOperStatus"    } }
          }
        }
      }
    }
  }
}
```

#### 6.3.2 Site_FGT-DRC

Gunakan query yang identik dengan DC; ubah dua filter berikut:

```json
{ "term":  { "tag.source":  "10.90.150.1"           } },
{ "terms": { "tag.ifIndex": ["39", "38", "7", "8", "18"] } }
```

#### 6.3.3 Site_FGT-Office

Gunakan query yang identik; ubah dua filter berikut:

```json
{ "term":  { "tag.source":  "10.10.10.10"           } },
{ "terms": { "tag.ifIndex": ["14", "15", "16", "17"] } }
```

*Office hanya 4 interface eksplisit. Jika interface ke-5 hadir di data,
`"size": 5` pada `by_interface` terms agg akan menangkapnya secara organik.*

---

### 6.4 SD-WAN SLA — Pre-fetch Validation Query

Jalankan query ini sebagai **check awal** sebelum merender panel SLA penuh.
Jika `hits.total.value === 0`, trigger `status = 'empty'` — jangan render panel.

**Index:** `telegraf-index-*`

```json
{
  "size": 0,
  "query": {
    "bool": {
      "filter": [
        {
          "range": {
            "@timestamp": { "gte": "<FROM>", "lte": "<TO>" }
          }
        },
        { "term": { "measurement_name": "fgt_perf_sla" } },
        { "term": { "tag.source":       "<SOURCE_IP>"  } }
      ]
    }
  },
  "track_total_hits": true
}
```

**API Layer Logic:**

```python
validation_result = opensearch.search(index="telegraf-index-*", body=validation_query)
total_hits = validation_result["hits"]["total"]["value"]

if total_hits == 0:
    return {"status": "empty", "message": "No SLA data for this site in selected range"}
else:
    # Proceed dengan full SLA query
    return await fetch_full_sdwan_sla(source_ip, time_range)
```

---

### 6.5 FortiGate HA Status Query

**Index:** `telegraf-index-*` | **Site:** FGT-DC only

```json
{
  "size": 10,
  "query": {
    "bool": {
      "filter": [
        {
          "range": {
            "@timestamp": {
              "gte": "now-5m",
              "lte": "now"
            }
          }
        },
        { "term": { "measurement_name": "fgt_ha"       } },
        { "term": { "tag.source":       "10.80.150.1"  } }
      ]
    }
  },
  "sort": [
    { "@timestamp": { "order": "desc" } }
  ],
  "_source": [
    "@timestamp",
    "fgt_ha.ha_mode",
    "fgt_ha.ha_member_count",
    "fgt_ha.ha_role",
    "fgt_ha.ha_sync_status",
    "fgt_ha.ha_priority",
    "tag.device"
  ]
}
```

> **⚠️ Field Verification Required:**
> Nama field `fgt_ha.*` bergantung pada Telegraf input plugin yang digunakan.
> Verifikasi nama field yang sebenarnya di OpenSearch DevTools sebelum implementasi:
>
> ```
> GET telegraf-index-*/_mapping/field/fgt_ha*,measurement_name
> ```
>
> Alternatif measurement name yang umum: `fgt_ha_stats`, `fortigate_ha`

---

## 7. Interface Mapping Reference

| Site | Source IP | ifIndex | Label | Tipe |
|---|---|---|---|---|
| FGT-DC | `10.80.150.1` | `39` | MPLS LinkNet | MPLS WAN |
| FGT-DC | `10.80.150.1` | `38` | MPLS iForte | MPLS WAN |
| FGT-DC | `10.80.150.1` | `3` | WAN LinkNet | Internet WAN |
| FGT-DC | `10.80.150.1` | `4` | WAN iForte | Internet WAN |
| FGT-DC | `10.80.150.1` | `18` | MPLS Dukcapil | MPLS WAN |
| FGT-DRC | `10.90.150.1` | `39` | MPLS LinkNet | MPLS WAN |
| FGT-DRC | `10.90.150.1` | `38` | MPLS iForte | MPLS WAN |
| FGT-DRC | `10.90.150.1` | `7` | WAN LinkNet | Internet WAN |
| FGT-DRC | `10.90.150.1` | `8` | WAN iForte | Internet WAN |
| FGT-DRC | `10.90.150.1` | `18` | MPLS Dukcapil | MPLS WAN |
| FGT-Office | `10.10.10.10` | `14` | MPLS LinkNet | MPLS WAN |
| FGT-Office | `10.10.10.10` | `15` | MPLS iForte | MPLS WAN |
| FGT-Office | `10.10.10.10` | `16` | WAN LDP | Internet WAN |
| FGT-Office | `10.10.10.10` | `17` | WAN iForte | Internet WAN |

---

## 8. Non-Functional Requirements

| Requirement | Target | Catatan |
|---|---|---|
| Query response time | < 3 detik | Untuk time range ≤ 24 jam |
| Chart render time | < 1 detik | Setelah data diterima dari API |
| Data freshness | ≤ 60 detik lag | Sesuai cadence IPFIX export FortiGate |
| Error recovery | Auto-retry 3× | Exponential backoff: 1s / 3s / 9s |
| Empty state | Wajib diimplementasi | Setiap komponen harus handle `length === 0` |
| Counter reset handling | Wajib diimplementasi | Delta negatif → `null` (bukan nilai negatif) |

---

## 9. Definition of Done

Sebuah fitur dianggap **Done** dan siap merge ke main branch ketika:

1. ✅ Seluruh Acceptance Criteria untuk modul tersebut terpenuhi
2. ✅ **Tidak ada perubahan** yang memengaruhi global layout (sidebar/navbar/footer)
3. ✅ Query diverifikasi di OpenSearch DevTools dengan data nyata dari environment staging
4. ✅ Komponen React ter-render tanpa error untuk semua 3 site (DC, DRC, Office)
5. ✅ Edge cases ditangani: empty data, query timeout, counter reset (Interface Stats)
6. ✅ `isAnimationActive={false}` dipasang pada semua `<Bar>` di Recharts
7. ✅ Code review oleh minimal 1 peer engineer
8. ✅ Tidak ada regresi pada modul VPN Sessions dan Sankey Diagram

---

## Appendix A — Semantic Field Reference

### Flow Data (`flow-*`)

| Field | Deskripsi | Tipe |
|---|---|---|
| `flow.export.ip.addr` | IP eksportir FortiGate — **primary site filter** | `keyword` |
| `flow.client.ip` | IP sisi penginisiasi koneksi (canonical) | `ip` |
| `flow.server.ip` | IP sisi penerima koneksi (canonical) | `ip` |
| `flow.app.name` | Nama aplikasi dari AppID engine FortiGate | `keyword` |
| `flow.bytes` | Total bytes per flow record | `long` |
| `flow.packets` | Total packets per flow record | `long` |
| `flow.id` | Unique flow identifier | `keyword` |

### Telegraf Device Metrics (`telegraf-index-*`)

| Field | Deskripsi | Tipe |
|---|---|---|
| `measurement_name` | Identifier measurement Telegraf | `keyword` |
| `tag.source` | IP device sumber — **primary device filter** | `keyword` |
| `tag.device` | Hostname device | `keyword` |
| `tag.ifIndex` | SNMP ifIndex interface | `keyword` |
| `tag.ifName` | Nama interface human-readable | `keyword` |
| `fgt_iface_stats.ifHCInOctets` | Counter kumulatif bytes inbound | `long` |
| `fgt_iface_stats.ifHCOutOctets` | Counter kumulatif bytes outbound | `long` |
| `fgt_iface_stats.ifHighSpeed_Mbps` | Kecepatan nominal interface | `long` |
| `fgt_iface_stats.ifOperStatus` | Status operasional (1=UP, 2=DOWN) | `integer` |
| `fgt_ha.ha_mode` | Mode HA (active-passive / active-active) | `keyword` |
| `fgt_ha.ha_member_count` | Jumlah anggota cluster HA | `integer` |
| `fgt_ha.ha_role` | Role member (primary / secondary) | `keyword` |
| `fgt_ha.ha_sync_status` | Status sinkronisasi HA | `keyword` |
| `fgt_ha.ha_priority` | Prioritas member dalam cluster | `integer` |

---

*Dokumen ini disusun berdasarkan brief Engineering Team NOD v2 — 12 Juni 2026.*
*Review selanjutnya: sebelum NOD Sprint-05 Planning.*
*Versi berikutnya (v2.1) akan mencakup: alert rule definitions dan RBAC per modul.*
