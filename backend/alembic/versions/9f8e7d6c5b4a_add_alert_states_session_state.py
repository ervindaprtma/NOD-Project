"""add alert_states.session_state for VPN session-monitor rules

A kind="session" rule tracks the set of currently-active VPN sessions between polls to
emit connect/disconnect events. The previous snapshot is stored here as JSONB
{username: {remote_ip, active_ip, started_at, device}}. Idempotent.

Revision ID: 9f8e7d6c5b4a
Revises: e9a1b2c3d4f5
Create Date: 2026-08-02 09:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "9f8e7d6c5b4a"
down_revision: Union[str, None] = "e9a1b2c3d4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("alert_states")}
    if "session_state" not in cols:
        op.add_column("alert_states", sa.Column("session_state", JSONB(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("alert_states")}
    if "session_state" in cols:
        op.drop_column("alert_states", "session_state")
