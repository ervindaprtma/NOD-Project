"""add is_active to notification_templates

Lets an admin activate/deactivate a message template without deleting it. Inactive
templates aren't offered to rules and aren't rendered (engine falls back to default /
hardcoded). Idempotent, matching this project's migration style.

Revision ID: e3c4d5f6a7b8
Revises: d2b3c4e5f6a7
Create Date: 2026-07-21 01:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e3c4d5f6a7b8"
down_revision: Union[str, None] = "d2b3c4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("notification_templates")}
    if "is_active" not in existing:
        op.add_column(
            "notification_templates",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("notification_templates")}
    if "is_active" in existing:
        op.drop_column("notification_templates", "is_active")
