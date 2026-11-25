"""ping_targets: curated Ping Monitor targets (plans/2026-10-21_ping-monitor.md §2).

Config only — live ICMP metrics are read from telegraf measurement=ping in
OpenSearch at request time. Identity is (url, cluster).

Revision ID: u6v7w8x9y0z1
Revises: t5u6v7w8x9y0
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "u6v7w8x9y0z1"
down_revision = "t5u6v7w8x9y0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ping_targets",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("url", sa.String(length=253), nullable=False,
                  comment="telegraf tag.url (IP or DNS name)"),
        sa.Column("cluster", sa.String(length=8), nullable=False,
                  comment="OpenSearch cluster that reports it: dc | drc"),
        sa.Column("display_name", sa.String(length=255), nullable=True,
                  comment="Admin-set name; NULL = tag.target_name / url"),
        sa.Column("group_kind", sa.String(length=16), nullable=False,
                  comment="internal | internet | dr"),
        sa.Column("expected_down", sa.Boolean(), server_default=sa.text("false"), nullable=False,
                  comment="Inactive DR link: shown slate, excluded from incident counts"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("url", "cluster", name="uq_ping_targets_url_cluster"),
    )


def downgrade() -> None:
    op.drop_table("ping_targets")
