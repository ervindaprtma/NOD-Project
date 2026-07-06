"""add v3 alert_template columns (category, icon, description, etc.)

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-07-04 18:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "h3i4j5k6l7m8"
down_revision: Union[str, None] = "g2h3i4j5k6l7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # v3 §3.12 — alert template enrichment for non-technical users.
    # All columns are NOT NULL with server_default so existing rows
    # (if any) automatically satisfy constraints. The application model
    # also has Python-side defaults that match these.
    #
    # NOTE: subject_template already exists in the DB schema (created in
    # initial_schema before v3.12 enrichment); it is intentionally skipped
    # here. All 8 new columns listed below are the actual drift.
    op.add_column(
        "alert_templates",
        sa.Column(
            "category",
            sa.String(length=20),
            nullable=False,
            server_default="performance",
        ),
    )
    op.create_index(
        "ix_alert_templates_category", "alert_templates", ["category"]
    )
    op.add_column(
        "alert_templates",
        sa.Column(
            "icon",
            sa.String(length=4),
            nullable=False,
            server_default=sa.text("'\U0001F4CA'"),  # 📊
        ),
    )
    op.add_column(
        "alert_templates",
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.add_column(
        "alert_templates",
        sa.Column(
            "underlying_kind",
            sa.String(length=10),
            nullable=False,
            server_default="single",
        ),
    )
    op.add_column(
        "alert_templates",
        sa.Column(
            "locked_fields",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "alert_templates",
        sa.Column(
            "exposed_fields",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "alert_templates",
        sa.Column(
            "is_user_created",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "alert_templates",
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("alert_templates", "sort_order")
    op.drop_column("alert_templates", "is_user_created")
    op.drop_column("alert_templates", "exposed_fields")
    op.drop_column("alert_templates", "locked_fields")
    op.drop_column("alert_templates", "underlying_kind")
    op.drop_column("alert_templates", "description")
    op.drop_column("alert_templates", "icon")
    op.drop_index("ix_alert_templates_category", table_name="alert_templates")
    op.drop_column("alert_templates", "category")