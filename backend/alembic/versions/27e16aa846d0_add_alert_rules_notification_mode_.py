"""add alert_rules notification_mode, renotify_enabled, notification_template_id (idempotent)

Closes the schema drift that made `SELECT ... FROM alert_rules` fail with
UndefinedColumnError: the AlertRule model declared these three columns but no
revision ever created them, so alembic reported "head" while the table lacked them.

Revision ID: 27e16aa846d0
Revises: k6l7m8n9o0p1
Create Date: 2026-07-17 04:29:01.577192
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "27e16aa846d0"
down_revision: Union[str, None] = "k6l7m8n9o0p1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Named explicitly: autogenerate emits create_foreign_key(None, ...), which lets
# Postgres pick the name and leaves downgrade's drop_constraint(None, ...) unable to
# run. Matches the existing alert_rules_<column>_fkey convention.
FK_NOTIFICATION_TEMPLATE = "alert_rules_notification_template_id_fkey"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    alert_rules_columns = {c["name"] for c in inspector.get_columns("alert_rules")}

    # Both NOT NULL columns get a server_default. alert_rules is empty on DC, but the
    # DRC/Office databases may already hold rules, and ADD COLUMN NOT NULL without a
    # default fails outright on a populated table. The model declares renotify_enabled
    # with a Python-side default only, so the default must be spelled out here or this
    # migration would succeed on DC and fail everywhere that has data.
    if "notification_mode" not in alert_rules_columns:
        op.add_column(
            "alert_rules",
            sa.Column(
                "notification_mode",
                sa.String(length=10),
                nullable=False,
                server_default=sa.text("'stateful'"),
            ),
        )

    if "renotify_enabled" not in alert_rules_columns:
        op.add_column(
            "alert_rules",
            sa.Column(
                "renotify_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )

    if "notification_template_id" not in alert_rules_columns:
        op.add_column(
            "alert_rules",
            sa.Column("notification_template_id", sa.UUID(as_uuid=False), nullable=True),
        )

    existing_fks = {fk.get("name") for fk in inspector.get_foreign_keys("alert_rules")}
    if FK_NOTIFICATION_TEMPLATE not in existing_fks:
        op.create_foreign_key(
            FK_NOTIFICATION_TEMPLATE,
            "alert_rules",
            "notification_templates",
            ["notification_template_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # notification_configs.channel — the model declares a unique index; the database has
    # a non-unique index plus a separate unique constraint. Uniqueness is already
    # enforced either way, so this changes no behaviour; it exists so `alembic check`
    # can run clean as a CI gate. Verified safe: the 3 existing rows (telegram,
    # whatsapp, discord) are distinct.
    nc_constraints = {c["name"] for c in inspector.get_unique_constraints("notification_configs")}
    if "notification_configs_channel_key" in nc_constraints:
        op.drop_constraint("notification_configs_channel_key", "notification_configs", type_="unique")

    nc_indexes = {i["name"]: i for i in inspector.get_indexes("notification_configs")}
    if not nc_indexes.get("ix_notification_configs_channel", {}).get("unique"):
        if "ix_notification_configs_channel" in nc_indexes:
            op.drop_index("ix_notification_configs_channel", table_name="notification_configs")
        op.create_index(
            "ix_notification_configs_channel", "notification_configs", ["channel"], unique=True
        )

    # Comment-only syncs. No data or behaviour change — they exist purely so
    # `alembic check` reports clean, which is what makes the CI gate meaningful.
    op.alter_column(
        "alert_rules", "clauses",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment='Composite rule clauses (P5). List of {"data_source", "metric_field", ...} dicts.',
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "alert_templates", "locked_fields",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="Fields hardcoded by this template (data_source, metric_field, etc.)",
        existing_nullable=False,
        existing_server_default=sa.text("'{}'::jsonb"),
    )
    op.alter_column(
        "alert_templates", "exposed_fields",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="Fields the user can set (e.g. ['threshold_value', 'site_name'])",
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "notification_configs", "config",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="Encrypted credentials + plaintext metadata",
        existing_nullable=False,
        existing_server_default=sa.text("'{}'::jsonb"),
    )
    op.alter_column(
        "notification_configs", "recipients",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="Optional per-group routing (e.g. DC alerts → DC group)",
        existing_nullable=True,
    )
    op.alter_column(
        "refresh_tokens", "source_ip",
        existing_type=sa.VARCHAR(length=45),
        comment="IP address of client that created this token",
        existing_comment="IP address of the client that created this token (IPv4 or IPv6)",
        existing_nullable=True,
    )
    op.alter_column(
        "report_jobs", "file_deleted",
        existing_type=sa.BOOLEAN(),
        comment="True if file was deleted from storage",
        existing_nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "report_jobs", "sites",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="List of site names to include in report",
        existing_nullable=True,
    )
    op.alter_column(
        "report_jobs", "sections",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment="List of report sections to include; None = all",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "report_jobs", "sections",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment=None,
        existing_comment="List of report sections to include; None = all",
        existing_nullable=True,
    )
    op.alter_column(
        "report_jobs", "sites",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment=None,
        existing_comment="List of site names to include in report",
        existing_nullable=True,
    )
    op.alter_column(
        "report_jobs", "file_deleted",
        existing_type=sa.BOOLEAN(),
        comment=None,
        existing_comment="True if file was deleted from storage",
        existing_nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "refresh_tokens", "source_ip",
        existing_type=sa.VARCHAR(length=45),
        comment="IP address of the client that created this token (IPv4 or IPv6)",
        existing_comment="IP address of client that created this token",
        existing_nullable=True,
    )
    op.alter_column(
        "notification_configs", "recipients",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment=None,
        existing_comment="Optional per-group routing (e.g. DC alerts → DC group)",
        existing_nullable=True,
    )
    op.alter_column(
        "notification_configs", "config",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment=None,
        existing_comment="Encrypted credentials + plaintext metadata",
        existing_nullable=False,
        existing_server_default=sa.text("'{}'::jsonb"),
    )
    op.alter_column(
        "alert_templates", "exposed_fields",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment=None,
        existing_comment="Fields the user can set (e.g. ['threshold_value', 'site_name'])",
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "alert_templates", "locked_fields",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment=None,
        existing_comment="Fields hardcoded by this template (data_source, metric_field, etc.)",
        existing_nullable=False,
        existing_server_default=sa.text("'{}'::jsonb"),
    )
    op.alter_column(
        "alert_rules", "clauses",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        comment=None,
        existing_comment='Composite rule clauses (P5). List of {"data_source", "metric_field", ...} dicts.',
        existing_nullable=False,
        existing_server_default=sa.text("'[]'::jsonb"),
    )

    op.drop_index("ix_notification_configs_channel", table_name="notification_configs")
    op.create_index(
        "ix_notification_configs_channel", "notification_configs", ["channel"], unique=False
    )
    op.create_unique_constraint(
        "notification_configs_channel_key", "notification_configs", ["channel"]
    )

    op.drop_constraint(FK_NOTIFICATION_TEMPLATE, "alert_rules", type_="foreignkey")
    op.drop_column("alert_rules", "notification_template_id")
    op.drop_column("alert_rules", "renotify_enabled")
    op.drop_column("alert_rules", "notification_mode")
