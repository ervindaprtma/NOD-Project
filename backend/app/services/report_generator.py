"""
Report Generator Service (FR-12).
Generates PDF, HTML, and DOCX reports with embedded Matplotlib charts.
Uses WeasyPrint (PDF), Jinja2 (HTML), python-docx (DOCX).

Report types:
  R-01: Traffic Flow Report
  R-02: Resource Usage Report
  R-03: Active VPN Users Report
  R-04: SD-WAN SLA Report
  R-05: Traffic Inbound Report
  R-06: Traffic Internal Report
  R-07: Executive Summary Report
  R-08: All-in-One Report (R-01 through R-07 combined)
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

# Sites used for per-site traffic in R-01 and for SD-WAN SLA queries
DEFAULT_SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]


async def generate_report(job: ReportJob) -> str:
    """
    Generate a report file for the given job.
    Returns the output file path.
    Raises Exception on failure.

    Report types:
      R-01: Traffic Flow Report
      R-02: Resource Usage Report
      R-03: Active VPN Users Report
      R-04: SD-WAN SLA Report
      R-05: Traffic Inbound Report
      R-06: Traffic Internal Report
      R-07: Executive Summary Report
      R-08: All-in-One Report (R-01 through R-07)
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
        "R-04": "SD-WAN SLA Report",
        "R-05": "Traffic Inbound Report",
        "R-06": "Traffic Internal Report",
        "R-07": "Executive Summary Report",
        "R-08": "All-in-One Network Observability Report",
    }
    return mapping.get(report_type, "NOD Report")


async def _build_report_context(
    report_type: str, gte_ms: int, lte_ms: int
) -> dict:
    """
    Fetch data from OpenSearch and build the report template context.
    Includes base64-encoded chart PNGs for embedding.
    """
    context: dict = {"charts": {}, "per_site_traffic": {}}
    from app.services.chart_renderer import render_bar_chart, render_timeseries_chart, render_vpn_bar_chart

    # ── R-01: Traffic Flow (enhanced) ────────────────────────────
    if report_type in ("R-01", "R-08"):
        try:
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
        except Exception as exc:
            logger.error("R-01 base traffic data fetch failed: %s", exc, exc_info=True)

        # R-01 Enhanced: Top AS Orgs
        try:
            from app.opensearch import appid as appid_qb
            top_as_orgs = await appid_qb.top_dst_as_orgs(gte_ms=gte_ms, lte_ms=lte_ms, size=10)
            context["top_as_orgs"] = top_as_orgs
            if top_as_orgs:
                labels = [o["org_name"] for o in top_as_orgs[:10]]
                values = [o["total_bytes"] for o in top_as_orgs[:10]]
                png_bytes = render_bar_chart(
                    labels, values,
                    title="Top 10 Destination AS Organizations",
                    xlabel="Bytes",
                )
                context["charts"]["top_as_orgs"] = base64.b64encode(png_bytes).decode()
        except Exception as exc:
            logger.error("R-01 top_as_orgs fetch failed: %s", exc, exc_info=True)

        # R-01 Enhanced: Top Countries
        try:
            from app.opensearch import appid as appid_qb
            top_countries = await appid_qb.top_dst_as_countries(gte_ms=gte_ms, lte_ms=lte_ms, size=10)
            context["top_countries"] = top_countries
            if top_countries:
                labels = [c["country"] for c in top_countries[:10]]
                values = [c["total_bytes"] for c in top_countries[:10]]
                png_bytes = render_bar_chart(
                    labels, values,
                    title="Top 10 Destination Countries",
                    xlabel="Bytes",
                )
                context["charts"]["top_countries"] = base64.b64encode(png_bytes).decode()
        except Exception as exc:
            logger.error("R-01 top_countries fetch failed: %s", exc, exc_info=True)

        # R-01 Enhanced: Protocol Distribution
        try:
            from app.opensearch import appid as appid_qb
            protocol_dist = await appid_qb.protocol_distribution(gte_ms=gte_ms, lte_ms=lte_ms)
            context["protocol_dist"] = protocol_dist
            if protocol_dist:
                labels = [p["protocol"] for p in protocol_dist[:10]]
                values = [p["total_bytes"] for p in protocol_dist[:10]]
                png_bytes = render_bar_chart(
                    labels, values,
                    title="Protocol Distribution by Traffic Volume",
                    xlabel="Bytes",
                )
                context["charts"]["protocol_dist"] = base64.b64encode(png_bytes).decode()
        except Exception as exc:
            logger.error("R-01 protocol_dist fetch failed: %s", exc, exc_info=True)

        # R-01 Enhanced: Per-site traffic flow summary
        for site in DEFAULT_SITES:
            try:
                from app.opensearch import traffic_flow as tf_qb
                site_data = await tf_qb.flow_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site
                )
                context["per_site_traffic"][site] = site_data
            except Exception as exc:
                logger.error("R-01 per-site traffic fetch failed for %s: %s", site, exc, exc_info=True)

    # ── R-02: Resource Usage ─────────────────────────────────────
    if report_type in ("R-02", "R-08"):
        try:
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
        except Exception as exc:
            logger.error("R-02 resource usage fetch failed: %s", exc, exc_info=True)

    # ── R-03: VPN Users ──────────────────────────────────────────
    if report_type in ("R-03", "R-08"):
        try:
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
        except Exception as exc:
            logger.error("R-03 VPN users fetch failed: %s", exc, exc_info=True)

    # ── R-04: SD-WAN SLA ────────────────────────────────────────
    if report_type in ("R-04", "R-08"):
        try:
            from app.opensearch import sdwan as sdwan_qb
            context["sla_data"] = {}
            context["sla_charts"] = {}

            sla_sites = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]

            for site in sla_sites:
                site_sla = {}

                # SLA Timeline for each metric
                for metric in ("latency", "jitter", "packet_loss"):
                    try:
                        timeline_data = await sdwan_qb.sla_timeline(
                            gte_ms=gte_ms, lte_ms=lte_ms,
                            site_name=site, metric=metric, interval="5m",
                        )
                        site_sla[f"{metric}_timeline"] = timeline_data

                        # Render multi-line timeseries chart for latency and jitter
                        if timeline_data and metric in ("latency", "jitter"):
                            chart_key = f"sla_{metric}_{site}"
                            png_bytes = render_timeseries_chart(
                                timeline_data,
                                title=f"SD-WAN {metric.title()} — {site}",
                                ylabel=metric.title(),
                                series_key="label",
                            )
                            context["sla_charts"][chart_key] = base64.b64encode(png_bytes).decode()
                    except Exception as exc:
                        logger.error("R-04 SLA %s timeline fetch failed for %s: %s", metric, site, exc, exc_info=True)

                # SLA Summary (compliance data)
                try:
                    summary = await sdwan_qb.sla_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
                    )
                    site_sla["summary"] = summary
                except Exception as exc:
                    logger.error("R-04 SLA summary fetch failed for %s: %s", site, exc, exc_info=True)

                context["sla_data"][site] = site_sla

        except Exception as exc:
            logger.error("R-04 SD-WAN SLA data fetch failed: %s", exc, exc_info=True)

    # ── R-05: Traffic Inbound ────────────────────────────────────
    if report_type in ("R-05", "R-08"):
        try:
            from app.opensearch import traffic_inbound as ti_qb
            context["inbound_data"] = {}

            inbound_sites = ["Site_FGT-DC", "Site_FGT-DRC"]
            for site in inbound_sites:
                try:
                    site_summary = await ti_qb.flow_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
                    )
                    context["inbound_data"][site] = site_summary

                    # Render bar charts for inbound data
                    # Top Services
                    if site_summary.get("top_services"):
                        svc_labels = [s["service_name"] for s in site_summary["top_services"][:10]]
                        svc_values = [s["total_bytes"] for s in site_summary["top_services"][:10]]
                        png_bytes = render_bar_chart(
                            svc_labels, svc_values,
                            title=f"Top Inbound Services — {site}",
                            xlabel="Bytes",
                        )
                        context["charts"][f"inbound_services_{site}"] = base64.b64encode(png_bytes).decode()

                    # Top Source AS Orgs
                    if site_summary.get("top_src_as_org"):
                        org_labels = [o["org_name"] for o in site_summary["top_src_as_org"][:10]]
                        org_values = [o["total_bytes"] for o in site_summary["top_src_as_org"][:10]]
                        png_bytes = render_bar_chart(
                            org_labels, org_values,
                            title=f"Top Source AS Organizations — {site}",
                            xlabel="Bytes",
                        )
                        context["charts"][f"inbound_src_org_{site}"] = base64.b64encode(png_bytes).decode()

                    # Top Source Countries
                    if site_summary.get("top_src_as_country"):
                        country_labels = [c["country"] for c in site_summary["top_src_as_country"][:10]]
                        country_values = [c["total_bytes"] for c in site_summary["top_src_as_country"][:10]]
                        png_bytes = render_bar_chart(
                            country_labels, country_values,
                            title=f"Top Source Countries — {site}",
                            xlabel="Bytes",
                        )
                        context["charts"][f"inbound_country_{site}"] = base64.b64encode(png_bytes).decode()

                    # Protocol Distribution
                    if site_summary.get("protocol_dist"):
                        proto_labels = [p["protocol"] for p in site_summary["protocol_dist"][:10]]
                        proto_values = [p["total_bytes"] for p in site_summary["protocol_dist"][:10]]
                        png_bytes = render_bar_chart(
                            proto_labels, proto_values,
                            title=f"Inbound Protocol Distribution — {site}",
                            xlabel="Bytes",
                        )
                        context["charts"][f"inbound_protocol_{site}"] = base64.b64encode(png_bytes).decode()

                    # Egress Breakdown
                    if site_summary.get("egress_breakdown"):
                        egr_labels = [e["interface"] for e in site_summary["egress_breakdown"][:10]]
                        egr_values = [e["total_bytes"] for e in site_summary["egress_breakdown"][:10]]
                        png_bytes = render_bar_chart(
                            egr_labels, egr_values,
                            title=f"Inbound Egress Interface Breakdown — {site}",
                            xlabel="Bytes",
                        )
                        context["charts"][f"inbound_egress_{site}"] = base64.b64encode(png_bytes).decode()

                except Exception as exc:
                    logger.error("R-05 inbound traffic fetch failed for %s: %s", site, exc, exc_info=True)

        except Exception as exc:
            logger.error("R-05 traffic inbound data fetch failed: %s", exc, exc_info=True)

    # ── R-06: Traffic Internal ───────────────────────────────────
    if report_type in ("R-06", "R-08"):
        try:
            from app.opensearch import traffic_internal as tint_qb
            context["internal_data"] = {}

            internal_sites = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]
            for site in internal_sites:
                try:
                    site_summary = await tint_qb.flow_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
                    )
                    context["internal_data"][site] = site_summary
                except Exception as exc:
                    logger.error("R-06 internal traffic fetch failed for %s: %s", site, exc, exc_info=True)

        except Exception as exc:
            logger.error("R-06 traffic internal data fetch failed: %s", exc, exc_info=True)

    # ── R-07: Executive Summary (combines key metrics) ──────────
    if report_type in ("R-07", "R-08"):
        try:
            context["executive_summary"] = _build_executive_summary(context)
        except Exception as exc:
            logger.error("R-07 executive summary build failed: %s", exc, exc_info=True)

    return context


def _build_executive_summary(context: dict) -> dict:
    """
    Build a single-page KPI summary from data already gathered in context.
    Works for R-07 standalone or R-08 (where context has all report data).
    """
    summary: dict = {}

    # Top 5 apps from R-01 data
    top_apps = context.get("top_applications", [])
    if top_apps:
        summary["top_5_apps"] = [
            {"app": a.get("application", ""), "bytes": a.get("total_bytes", 0)}
            for a in top_apps[:5]
        ]

    # Device health from R-02 data
    devices = context.get("devices", [])
    if devices:
        healthy = sum(1 for d in devices if d.get("cpu_usage", 0) < 80 and d.get("mem_usage", 0) < 80)
        summary["device_health"] = {
            "total_devices": len(devices),
            "healthy": healthy,
            "degraded": len(devices) - healthy,
        }

    # VPN counts from R-03 data
    summary["vpn_summary"] = {
        "ssl_vpn_count": context.get("ssl_vpn_count", 0),
        "ipsec_vpn_count": context.get("ipsec_vpn_count", 0),
    }

    # SLA summary from R-04 data
    sla_data = context.get("sla_data", {})
    if sla_data:
        sla_summaries = {}
        for site, site_sla in sla_data.items():
            site_summary = site_sla.get("summary", {})
            if site_summary:
                sla_summaries[site] = {
                    "avg_latency": site_summary.get("avg_latency", []),
                    "avg_jitter": site_summary.get("avg_jitter", []),
                    "avg_packet_loss": site_summary.get("avg_packet_loss", []),
                    "labels": site_summary.get("labels", []),
                }
        summary["sla_summary"] = sla_summaries

    # Total throughput
    summary["total_throughput_bytes"] = context.get("total_throughput_bytes", 0)

    return summary


# ─────────────────────────────────────────────────────────────────
# Format Generators
# ─────────────────────────────────────────────────────────────────


def _generate_html(context: dict, output_path: Path) -> None:
    """Generate a self-contained HTML report using Jinja2.

    Loads the template from reports/templates/report_base.html.
    The template follows the NOD design system and is WeasyPrint-compatible.
    """
    from jinja2 import Environment, FileSystemLoader

    template_path = TEMPLATE_DIR
    env = Environment(
        loader=FileSystemLoader(str(template_path)),
        autoescape=True,
    )
    template = env.get_template("report_base.html")
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
    """Generate DOCX report using the comprehensive NOD DOCX generator."""
    from scripts.generate_docx import generate_docx_report

    # Convert the backend context format to the expected JSON context format.
    # The backend stores data under flat keys (context["charts"], context["devices"], etc.)
    # while the generator expects context["report_meta"] and context["sections"].
    report_type = context.get("report_type", "R-XX")

    docx_context = {
        "report_meta": {
            "report_type": report_type,
            "title": context.get("report_title", "NOD Report"),
            "site": context.get("site", ""),
            "generated_at": context.get("generated_at", ""),
            "generated_by": context.get("generated_by", ""),
            "job_id": context.get("job_id", ""),
            "time_range": context.get("time_range", {}),
        },
        # Pass through all backend-specific keys so report builders can access them
        "charts": context.get("charts", {}),
        "devices": context.get("devices", []),
        "per_site_traffic": context.get("per_site_traffic", {}),
        "total_throughput_bytes": context.get("total_throughput_bytes", 0),
        "top_applications": context.get("top_applications", []),
        "top_as_orgs": context.get("top_as_orgs", []),
        "top_countries": context.get("top_countries", []),
        "protocol_dist": context.get("protocol_dist", []),
        "ssl_vpn_count": context.get("ssl_vpn_count", 0),
        "ipsec_vpn_count": context.get("ipsec_vpn_count", 0),
        "sla_data": context.get("sla_data", {}),
        "sla_charts": context.get("sla_charts", {}),
        "inbound_data": context.get("inbound_data", {}),
        "internal_data": context.get("internal_data", {}),
        "executive_summary": context.get("executive_summary", {}),
    }

    # Also support the generic sections format if present
    if "sections" in context:
        docx_context["sections"] = context["sections"]

    generate_docx_report(docx_context, output_path)
