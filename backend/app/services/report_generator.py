"""
Report Generator Service (FR-12).
Generates PDF, HTML, and DOCX reports with embedded Matplotlib charts.
Uses WeasyPrint (PDF), Jinja2 (HTML), python-docx (DOCX).
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from weasyprint import HTML

from app.core.config import get_settings
from app.db.models import ReportJob

settings = get_settings()
logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path("reports/templates")


async def generate_report(job: ReportJob) -> str:
    """
    Generate a report file for the given job.
    Returns the output file path.
    Raises Exception on failure.

    Report types:
      R-01: Traffic Flow Report
      R-02: Resource Usage Report
      R-03: Active VPN Users Report
      R-04: All-in-One Report
    """
    output_dir = Path("reports/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job.id}.{job.output_format}"

    # Fetch data from OpenSearch
    gte_ms = int(job.time_range_start.timestamp() * 1000)
    lte_ms = int(job.time_range_end.timestamp() * 1000)

    context = await _build_report_context(job.report_type, gte_ms, lte_ms)
    context["report_title"] = _report_title(job.report_type)
    context["generated_at"] = datetime.now(timezone.utc).isoformat()
    context["generated_by"] = str(job.created_by)
    context["job_id"] = str(job.id)
    context["time_range"] = f"{job.time_range_start.isoformat()} — {job.time_range_end.isoformat()}"

    match job.output_format:
        case "html":
            _generate_html(context, output_path)
        case "pdf":
            _generate_pdf(context, output_path)
        case "docx":
            _generate_docx(context, output_path)
        case _:
            raise ValueError(f"Unsupported output format: {job.output_format}")

    logger.info(f"Report generated: {output_path}")
    return str(output_path)


def _report_title(report_type: str) -> str:
    mapping = {
        "R-01": "Traffic Flow Report",
        "R-02": "Resource Usage Report",
        "R-03": "Active VPN Users Report",
        "R-04": "All-in-One Network Observability Report",
    }
    return mapping.get(report_type, "NOD Report")


async def _build_report_context(
    report_type: str, gte_ms: int, lte_ms: int
) -> dict:
    """
    Fetch data from OpenSearch and build the report template context.
    Includes base64-encoded chart PNGs for embedding.
    """
    context: dict = {"charts": {}}
    from app.services.chart_renderer import render_bar_chart, render_timeseries_chart, render_vpn_bar_chart

    # R-01: Traffic Flow
    if report_type in ("R-01", "R-04"):
        from app.opensearch import appid as appid_qb

        # Top Applications bar chart
        top_apps = await appid_qb.top_applications(gte_ms=gte_ms, lte_ms=lte_ms, size=10)
        if top_apps:
            labels = [a["application"] for a in top_apps[:10]]
            values = [a["total_bytes"] for a in top_apps[:10]]
            png_bytes = render_bar_chart(
                labels, values,
                title="Top 10 Applications by Traffic Volume",
                xlabel="Bytes",
            )
            context["charts"]["top_applications"] = base64.b64encode(png_bytes).decode()

        # Throughput timeline
        tp_data = await appid_qb.throughput_timeline(
            gte_ms=gte_ms, lte_ms=lte_ms, interval="15m"
        )
        if tp_data:
            png_bytes = render_timeseries_chart(
                tp_data,
                title="Throughput Over Time",
                ylabel="Bytes",
            )
            context["charts"]["throughput"] = base64.b64encode(png_bytes).decode()

        # Total throughput
        total_bytes = await appid_qb.total_throughput(gte_ms=gte_ms, lte_ms=lte_ms)
        context["total_throughput_bytes"] = total_bytes
        context["top_applications"] = top_apps

    # R-02: Resource Usage
    if report_type in ("R-02", "R-04"):
        from app.opensearch import ha as ha_qb

        devices = await ha_qb.current_device_status(gte_ms=gte_ms, lte_ms=lte_ms)
        context["devices"] = devices

        timeline = await ha_qb.resource_timeline(
            gte_ms=gte_ms, lte_ms=lte_ms, interval="15m"
        )
        if timeline["cpu"]:
            png_bytes = render_timeseries_chart(
                timeline["cpu"],
                title="CPU Usage Over Time",
                ylabel="CPU %",
                series_key="device",
            )
            context["charts"]["cpu_timeline"] = base64.b64encode(png_bytes).decode()

    # R-03: VPN Users
    if report_type in ("R-03", "R-04"):
        from app.opensearch import sslvpn as sslvpn_qb
        from app.opensearch import ipsec as ipsec_qb

        ssl_count = await sslvpn_qb.all_sslvpn_users_count(
            gte_ms=gte_ms, lte_ms=lte_ms, site_names=settings.sslvpn_sites_list
        )
        ipsec_count = await ipsec_qb.active_ipsec_users_count(
            gte_ms=gte_ms, lte_ms=lte_ms
        )
        context["ssl_vpn_count"] = ssl_count
        context["ipsec_vpn_count"] = ipsec_count

        # VPN bar chart
        if ssl_count > 0 or ipsec_count > 0:
            png_bytes = render_vpn_bar_chart(
                ssl_count=ssl_count or 0,
                ipsec_count=ipsec_count or 0,
            )
            context["charts"]["vpn_users"] = base64.b64encode(png_bytes).decode()

    return context


# ─────────────────────────────────────────────────────────────────
# Format Generators
# ─────────────────────────────────────────────────────────────────


def _generate_html(context: dict, output_path: Path) -> None:
    """Generate a self-contained HTML report using Jinja2."""
    from jinja2 import Template

    template_str = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{{ report_title }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; color: #1a1a1a; }
        h1 { color: #2563eb; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }
        h2 { color: #374151; margin-top: 32px; }
        .meta { color: #6b7280; font-size: 14px; margin-bottom: 24px; }
        table { border-collapse: collapse; width: 100%; margin: 16px 0; }
        th, td { border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; }
        th { background: #f3f4f6; font-weight: 600; }
        .chart-img { max-width: 100%; margin: 16px 0; }
        .kpi { font-size: 24px; font-weight: bold; color: #2563eb; }
        .footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #9ca3af; }
        @page { @bottom-center { content: "Page " counter(page) " of " counter(pages); font-size: 10px; color: #9ca3af; } }
    </style>
</head>
<body>
    <h1>{{ report_title }}</h1>
    <div class="meta">
        Generated: {{ generated_at }} | By: {{ generated_by }} | ID: {{ job_id }}<br>
        Time Range: {{ time_range }}
    </div>

    <h2>Executive Summary</h2>
    <p>Total Throughput: {{ total_throughput_bytes or 0 }} bytes</p>
    <p>Active SSL VPN Users: {{ ssl_vpn_count or 0 }}</p>
    <p>Active IPsec VPN Users: {{ ipsec_vpn_count or 0 }}</p>

    {% if charts.top_applications %}
    <h2>Top Applications</h2>
    <img class="chart-img" src="data:image/png;base64,{{ charts.top_applications }}" alt="Top Applications Chart">
    {% endif %}

    {% if charts.throughput %}
    <h2>Throughput Timeline</h2>
    <img class="chart-img" src="data:image/png;base64,{{ charts.throughput }}" alt="Throughput Chart">
    {% endif %}

    {% if charts.vpn_users %}
    <h2>Active VPN Users</h2>
    <img class="chart-img" src="data:image/png;base64,{{ charts.vpn_users }}" alt="VPN Users Chart">
    {% endif %}

    {% if devices %}
    <h2>Device Resource Status</h2>
    <table>
        <tr><th>Device</th><th>CPU %</th><th>Memory %</th><th>Sessions</th><th>Sync</th></tr>
        {% for d in devices %}
        <tr>
            <td>{{ d.device }} ({{ d.hostname }})</td>
            <td>{{ d.cpu_usage }}</td>
            <td>{{ d.mem_usage }}</td>
            <td>{{ d.session_count }}</td>
            <td>{{ d.sync_status }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}

    <div class="footer">
        NOD — Network Observability Dashboard | Document Classification: Internal — Confidential
    </div>
</body>
</html>"""

    template = Template(template_str)
    html_content = template.render(**context)
    output_path.write_text(html_content, encoding="utf-8")


def _generate_pdf(context: dict, output_path: Path) -> None:
    """Generate PDF via WeasyPrint from HTML template."""
    import tempfile

    # Generate HTML first, then convert to PDF
    html_path = output_path.with_suffix(".html.tmp")
    _generate_html(context, html_path)

    try:
        HTML(filename=str(html_path)).write_pdf(str(output_path))
    finally:
        if html_path.exists():
            html_path.unlink()  # Clean up temp HTML


def _generate_docx(context: dict, output_path: Path) -> None:
    """Generate DOCX report using python-docx."""
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # Title
    title = doc.add_heading(context.get("report_title", "NOD Report"), level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Meta
    doc.add_paragraph(
        f"Generated: {context.get('generated_at', '')} | By: {context.get('generated_by', '')} | ID: {context.get('job_id', '')}"
    ).style = "Normal"
    doc.add_paragraph(f"Time Range: {context.get('time_range', '')}").style = "Normal"

    # Executive Summary
    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph(f"Total Throughput: {context.get('total_throughput_bytes', 0):,} bytes")
    doc.add_paragraph(f"Active SSL VPN Users: {context.get('ssl_vpn_count', 0)}")
    doc.add_paragraph(f"Active IPsec VPN Users: {context.get('ipsec_vpn_count', 0)}")

    # Devices table
    devices = context.get("devices", [])
    if devices:
        doc.add_heading("Device Resource Status", level=1)
        table = doc.add_table(rows=1, cols=5, style="Table Grid")
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "Device"
        hdr_cells[1].text = "CPU %"
        hdr_cells[2].text = "Memory %"
        hdr_cells[3].text = "Sessions"
        hdr_cells[4].text = "Sync Status"

        for d in devices:
            row_cells = table.add_row().cells
            row_cells[0].text = f"{d.get('device', '')} ({d.get('hostname', '')})"
            row_cells[1].text = str(d.get("cpu_usage", ""))
            row_cells[2].text = str(d.get("mem_usage", ""))
            row_cells[3].text = str(d.get("session_count", ""))
            row_cells[4].text = d.get("sync_status", "")

    # Footer
    doc.add_page_break()
    doc.add_paragraph(
        "NOD — Network Observability Dashboard | Internal — Confidential",
        style="Normal",
    )

    doc.save(str(output_path))
