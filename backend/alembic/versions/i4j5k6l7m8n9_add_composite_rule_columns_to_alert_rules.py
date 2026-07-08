"""add composite-rule columns to alert_rules (kind, notify_when, clauses)

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-07-06 03:10:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "i4j5k6l7m8n9"
down_revision: Union[str, None] = "h3i4j5k6l7m8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P5 — composite rule support
    op.add_column(
        "alert_rules",
        sa.Column(
            "kind",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'single'"),
        ),
    )
    op.add_column(
        "alert_rules",
        sa.Column(
            "notify_when",
            sa.String(length=4),
            nullable=False,
            server_default=sa.text("'any'"),
        ),
    )
    op.add_column(
        "alert_rules",
        sa.Column(
            "clauses",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("alert_rules", "clauses")
    op.drop_column("alert_rules", "notify_when")
    op.drop_column("alert_rules", "kind")