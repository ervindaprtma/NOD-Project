"""add alert_rules.target_key for interface_stats sub-entity (Phase E)

interface_stats rules select an interface by ifIndex; unlike sdwan (link encoded in
metric_field) that doesn't generalize across sites, so it gets its own nullable column.
Idempotent (inspector-guarded) to match this project's migration style.

Revision ID: d2b3c4e5f6a7
Revises: c1a2b3d4e5f6
Create Date: 2026-07-21 00:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2b3c4e5f6a7"
down_revision: Union[str, None] = "c1a2b3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "target_key" not in existing:
        op.add_column("alert_rules", sa.Column("target_key", sa.String(64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "target_key" in existing:
        op.drop_column("alert_rules", "target_key")
