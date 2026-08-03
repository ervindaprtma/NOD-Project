"""add alert_rules.renotify_interval_minutes for per-rule re-notify cadence

While a threshold rule stays FIRING it re-sends a reminder every
ALERT_RENOTIFY_INTERVAL_MINUTES (global default). This column overrides that per rule:
NULL = inherit the global default, 0 = notify once (no reminders), N>0 = every N minutes.
Idempotent.

Revision ID: f5a6b7c8d9e0
Revises: 9f8e7d6c5b4a
Create Date: 2026-08-03 11:24:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "9f8e7d6c5b4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "renotify_interval_minutes" not in cols:
        op.add_column(
            "alert_rules",
            sa.Column("renotify_interval_minutes", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "renotify_interval_minutes" in cols:
        op.drop_column("alert_rules", "renotify_interval_minutes")
