"""device_ip_aliases: old_ip → current_ip mapping for re-IPed devices.

Availability (device_uptime) identity is tag.source (IP). When a device is
re-IPed, the old IP's history would orphan and the new IP would show as a
fresh device with partial_history. An explicit alias row lets the roster
query include both IPs so eras stitch under one device card — the same
two-phase idea as site_migration, but keyed on IP-alias.

Seed row: F121G-Office re-IP 2026-08-28 (verified live: old IP silent,
new IP flowing, hostname unchanged).

Revision ID: l7m8n9o0p1q2
Revises: e6f7a8b9c0d1
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa

revision = "l7m8n9o0p1q2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_ip_aliases",
        sa.Column("old_ip", sa.String(length=45), primary_key=True),
        sa.Column("current_ip", sa.String(length=45), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    op.create_index("ix_device_ip_aliases_current_ip", "device_ip_aliases", ["current_ip"])
    # Seed: F121G-Office re-IP (idempotent — PK on old_ip makes re-runs safe)
    op.execute(
        "INSERT INTO device_ip_aliases (old_ip, current_ip, hostname, note) "
        "VALUES ('10.10.10.10', '10.70.150.1', 'F121G-Office', "
        "'FGT-OFFICE re-IP 2026-08-28; ifIndexes 16/17/38/39 unchanged') "
        "ON CONFLICT (old_ip) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("device_ip_aliases")
