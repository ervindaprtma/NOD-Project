"""add observability columns to alert_states (Phase C)

Stamps per-evaluation visibility onto each rule's state row so the UI can show
"is this rule actually being monitored?": last_evaluated_at / last_value /
last_state_change_at / last_read_degraded. Idempotent (inspector-guarded) to match
the rest of this project's migrations.

Revision ID: c1a2b3d4e5f6
Revises: b8d4c1e90a37
Create Date: 2026-07-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, None] = "b8d4c1e90a37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("last_evaluated_at", lambda: sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True)),
    ("last_value", lambda: sa.Column("last_value", sa.Float(), nullable=True)),
    ("last_state_change_at", lambda: sa.Column("last_state_change_at", sa.DateTime(timezone=True), nullable=True)),
    ("last_read_degraded", lambda: sa.Column("last_read_degraded", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("alert_states")}
    for name, col in _COLUMNS:
        if name not in existing:
            op.add_column("alert_states", col())


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("alert_states")}
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("alert_states", name)
