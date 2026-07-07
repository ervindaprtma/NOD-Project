"""create notification_configs and maintenance_windows tables

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-07-07 08:50:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = "j5k6l7m8n9o0"
down_revision: Union[str, None] = "i4j5k6l7m8n9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # v3 §3.13 — notification channel configs (encrypted credentials)
    op.create_table(
        "notification_configs",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("channel", sa.String(length=20), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("min_severity", sa.String(length=20), nullable=False, server_default=sa.text("'CRITICAL'")),
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("recipients", JSONB, nullable=True),
        sa.Column("updated_by", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_notification_configs_channel", "notification_configs", ["channel"])

    # v3 §3.14 — maintenance windows (alert suppression)
    op.create_table(
        "maintenance_windows",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("site_name", sa.String(length=128), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_by", UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_maintenance_windows_site_name", "maintenance_windows", ["site_name"])
    op.create_index("ix_maintenance_windows_starts_at", "maintenance_windows", ["starts_at"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_windows_starts_at", table_name="maintenance_windows")
    op.drop_index("ix_maintenance_windows_site_name", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")
    op.drop_index("ix_notification_configs_channel", table_name="notification_configs")
    op.drop_table("notification_configs")