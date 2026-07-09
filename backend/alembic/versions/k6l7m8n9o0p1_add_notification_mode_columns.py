"""add default_notification_mode and locks_notification_mode (idempotent)

Revision ID: k6l7m8n9o0p1
Revises: 8c1d2e3f4a5b
Create Date: 2026-07-09 02:20:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k6l7m8n9o0p1"
down_revision: Union[str, None] = "8c1d2e3f4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("alert_templates")}

    if "default_notification_mode" not in existing_columns:
        op.add_column(
            "alert_templates",
            sa.Column(
                "default_notification_mode",
                sa.String(length=10),
                nullable=False,
                server_default=sa.text("'stateful'"),
                comment="stateful | peak_only",
            ),
        )

    if "locks_notification_mode" not in existing_columns:
        op.add_column(
            "alert_templates",
            sa.Column(
                "locks_notification_mode",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
                comment="whether to lock mode for this template",
            ),
        )


def downgrade() -> None:
    op.drop_column("alert_templates", "locks_notification_mode")
    op.drop_column("alert_templates", "default_notification_mode")
