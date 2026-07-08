"""add §11 tables: alert_field_catalog, notification_templates

Revision ID: 8c1d2e3f4a5b
Revises: j5k6l7m8n9o0
Create Date: 2026-07-08 17:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "8c1d2e3f4a5b"
down_revision: Union[str, None] = "j5k6l7m8n9o0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # §11.2 — alert field catalog table for guided rule creation.
    # Idempotent: skip if table already exists from a partial prior run.
    if "alert_field_catalog" not in existing_tables:
        op.create_table(
            "alert_field_catalog",
            sa.Column("id", UUID(as_uuid=False), nullable=False),
            sa.Column("data_source", sa.String(length=20), nullable=False),
            sa.Column("field_key", sa.String(length=255), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("unit", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("category", sa.String(length=10), nullable=False),
            sa.Column(
                "valid_aggregations",
                JSONB,
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "valid_conditions",
                JSONB,
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("example_threshold", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("data_source", "field_key", name="uq_field_catalog"),
        )
        op.create_index(
            "ix_alert_field_catalog_data_source",
            "alert_field_catalog",
            ["data_source"],
        )

    # §11.3 — notification templates table for channel-message templates.
    op.create_table(
        "notification_templates",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "subject_template",
            sa.Text(),
            nullable=False,
            server_default="Alert: {{ rule.name }}",
        ),
        sa.Column("body_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("line_template", sa.Text(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_user_created",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("notification_templates")
    op.drop_index("ix_alert_field_catalog_data_source", table_name="alert_field_catalog")
    op.drop_table("alert_field_catalog")