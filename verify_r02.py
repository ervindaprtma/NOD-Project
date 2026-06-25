import asyncio, time
from pathlib import Path
from datetime import datetime, timezone
from app.services.report_generator import (
    build_report_context,
    generate_html,
    generate_pdf,
    _generate_docx,
    _report_title,
    format_time_ms,
)


async def main():
    now_utc = int(time.time() * 1000)
    twelve_h_ago = now_utc - (12 * 3600 * 1000)

    print("Building R-02 context (all sites)...")
    ctx = await build_report_context(
        report_type="R-02",
        gte_ms=twelve_h_ago,
        lte_ms=now_utc,
        sites=["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"],
        sections=None,
    )

    # Add metadata that the report API normally provides
    ctx["report_title"] = _report_title("R-02")
    ctx["generated_at"] = format_time_ms(
        int(datetime.now(timezone.utc).timestamp() * 1000)
    )
    ctx["generated_by"] = "admin_noc"
    ctx["job_id"] = "verify-r02"
    ctx["report_type"] = "R-02"
    ctx["site_name"] = "All Sites (DC + DRC + Office)"
    ctx["site_id"] = "all"
    ctx["is_first_page"] = True
    ctx["total_sites"] = 1
    ctx["page_number"] = 1

    ru = ctx.get("report_data", {}).get("resource_usage", {})
    devices = ru.get("devices", [])
    print(f"Devices: {len(devices)}")
    for d in devices:
        print(
            f"  [{d.get('site')}] {d.get('device')} CPU={d.get('cpu_usage')}% Mem={d.get('mem_usage')}% Sessions={d.get('sessions')} Sync={d.get('sync_status')}"
        )

    outdir = Path("/app/reports/output")
    outdir.mkdir(parents=True, exist_ok=True)

    # Generate all 3 formats
    html_path = generate_html(ctx, outdir / "verify-r02-all.html")
    print(f"HTML: {html_path} ({html_path.stat().st_size:,} bytes)")

    pdf_path = generate_pdf(ctx, outdir / "verify-r02-all.pdf")
    print(f"PDF:  {pdf_path} ({pdf_path.stat().st_size:,} bytes)")

    docx_path = outdir / "verify-r02-all.docx"
    _generate_docx(ctx, docx_path)
    print(f"DOCX: {docx_path} ({docx_path.stat().st_size:,} bytes)")

    print("All 3 formats generated!")


if __name__ == "__main__":
    asyncio.run(main())