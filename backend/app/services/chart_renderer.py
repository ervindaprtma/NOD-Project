"""
Server-side chart rendering using Matplotlib.
Generates PNG charts for embedding in PDF/HTML/DOCX reports.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.dates as mdates


def _format_bytes_auto(n: float) -> str:
    if n < 1024:
        return f"{n:.0f} B"
    elif n < 1024**2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    else:
        return f"{n / 1024**3:.2f} GB"


def _format_x_axis(ax, data: list[dict], x_key: str):
    """Format x-axis timestamps. If data contains epoch millis, convert to datetime."""
    if not data:
        return
    sample = data[0].get(x_key, 0)
    if isinstance(sample, (int, float)) and sample > 1e12:
        xs = [datetime.fromtimestamp(p[x_key] / 1000, tz=timezone.utc) for p in data]
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig = ax.get_figure()
        if fig:
            fig.autofmt_xdate(rotation=30, ha='right')


def _format_bytes_auto(n: float) -> str:
    if n < 1024:
        return f"{n:.0f} B"
    elif n < 1024**2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    else:
        return f"{n / 1024**3:.2f} GB"


def render_timeseries_chart(
    data: list[dict],
    title: str,
    ylabel: str,
    x_key: str = "timestamp",
    y_key: str = "value",
    series_key: Optional[str] = None,
    width: int = 800,
    height: int = 400,
    dpi: int = 150,
) -> bytes:
    """
    Render a timeseries line chart.
    Returns PNG bytes.
    """
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)

    if series_key:
        # Multiple series — one line per unique series_key value
        series_values: dict[str, list[tuple]] = {}
        for point in data:
            key = point.get(series_key, "default")
            ts = point.get(x_key, 0)
            # Convert epoch ms to datetime if needed
            if isinstance(ts, (int, float)) and ts > 1e12:
                ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            series_values.setdefault(key, []).append(
                (ts, point.get(y_key, 0))
            )
        for label, points in series_values.items():
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            ax.plot(xs, ys, label=label, linewidth=1.2)
        ax.legend(loc="upper left", fontsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        fig.autofmt_xdate(rotation=30, ha='right')
    else:
        xs = []
        ys = []
        for p in data:
            ts = p.get(x_key, 0)
            if isinstance(ts, (int, float)) and ts > 1e12:
                ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            xs.append(ts)
            ys.append(p.get(y_key, 0))
        ax.plot(xs, ys, linewidth=1.2, color="#2563eb")
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        fig.autofmt_xdate(rotation=30, ha='right')

    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=10)
    ax.tick_params(axis="both", labelsize=8)
    ax.grid(True, alpha=0.3, linestyle="--")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_bar_chart(
    labels: list[str],
    values: list[float],
    title: str,
    xlabel: str = "",
    ylabel: str = "",
    horizontal: bool = True,
    width: int = 800,
    height: int = 400,
    dpi: int = 150,
) -> bytes:
    """
    Render a horizontal or vertical bar chart.
    """
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)

    if horizontal:
        y_pos = range(len(labels))
        ax.barh(y_pos, values, color="#2563eb", height=0.6)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8)
        ax.invert_yaxis()
    else:
        ax.bar(labels, values, color="#2563eb", width=0.6)
        ax.tick_params(axis="x", labelsize=8, rotation=45)

    ax.set_title(title, fontsize=12, fontweight="bold")
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=10)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.3, linestyle="--", axis="x" if horizontal else "y")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_vpn_bar_chart(
    ssl_count: int,
    ipsec_count: int,
    title: str = "Active VPN Users",
    width: int = 600,
    height: int = 300,
    dpi: int = 150,
) -> bytes:
    """Render a bar chart comparing SSL VPN and IPsec VPN active user counts."""
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)

    labels = ["SSL VPN", "IPsec VPN"]
    values = [ssl_count, ipsec_count]
    colors = ["#2563eb", "#7c3aed"]
    bars = ax.bar(labels, values, color=colors, width=0.5)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                str(val), ha="center", fontsize=12, fontweight="bold")

    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_ylabel("Active Users", fontsize=10)
    ax.tick_params(axis="both", labelsize=10)
    ax.grid(True, alpha=0.3, linestyle="--", axis="y")
    ax.set_ylim(0, max(max(values), 1) * 1.3)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_pie_chart(
    labels: list[str],
    values: list[float],
    title: str,
    width: int = 600,
    height: int = 400,
    dpi: int = 150,
) -> bytes:
    """Render a pie chart for protocol distribution or traffic type breakdown."""
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    colors = ["#2563eb", "#7c3aed", "#f59e0b", "#10b981", "#ef4444", "#06b6d4", "#8b5cf6", "#f97316"]
    wedges, texts, autotexts = ax.pie(
        values, labels=labels, autopct="%1.1f%%",
        colors=colors[:len(labels)], startangle=90,
        textprops={"fontsize": 8},
    )
    for t in autotexts:
        t.set_fontsize(7)
    ax.set_title(title, fontsize=12, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_gauge_chart(
    label: str,
    value: float,
    max_value: float = 100,
    unit: str = "%",
    width: int = 300,
    height: int = 200,
    dpi: int = 150,
) -> bytes:
    """Render a gauge/donut chart for KPI values like CPU/Memory usage."""
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    pct = min(100, max(0, (value / max_value) * 100))
    if pct < 60:
        color = "#10b981"
    elif pct < 80:
        color = "#f59e0b"
    else:
        color = "#ef4444"
    remaining = 100 - pct
    ax.pie([pct, remaining], colors=[color, "#e5e7eb"], startangle=90,
           counterclock=False, wedgeprops={"width": 0.3})
    ax.text(0, 0, f"{value:.1f}{unit}", ha="center", va="center", fontsize=16, fontweight="bold", color=color)
    ax.set_title(label, fontsize=10, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_stacked_area_chart(
    data: list[dict],
    series_names: list[str],
    title: str,
    ylabel: str = "Mbps",
    x_key: str = "timestamp",
    width: int = 800,
    height: int = 400,
    dpi: int = 150,
) -> bytes:
    """Render a stacked area chart for multi-series throughput (In/Out)."""
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    colors = ["#2563eb", "#f97316", "#10b981", "#7c3aed"]
    xs = []
    series_data = {name: [] for name in series_names}
    for point in data:
        ts = point.get(x_key, 0)
        if isinstance(ts, (int, float)) and ts > 1e12:
            ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        xs.append(ts)
        for name in series_names:
            series_data[name].append(point.get(name, 0))
    ax.stackplot(xs, *[series_data[name] for name in series_names],
                 labels=series_names, colors=colors[:len(series_names)], alpha=0.8)
    ax.legend(loc="upper left", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=30, ha="right")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.3, linestyle="--")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
