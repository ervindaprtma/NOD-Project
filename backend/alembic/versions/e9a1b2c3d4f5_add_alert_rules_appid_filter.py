"""add alert_rules.appid_filter for app/protocol/port scoping

appid_flow rules can now narrow the metric to a specific application
(flow.application.name), protocol (l4.proto.name), and/or destination port
(flow.server.l4.port.id). Stored as one nullable JSONB {app, protocol, port}.
Idempotent (inspector-guarded) to match this project's migration style.

Revision ID: e9a1b2c3d4f5
Revises: c8f3a2d1e5b9
Create Date: 2026-08-01 20:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e9a1b2c3d4f5"
down_revision: Union[str, None] = "c8f3a2d1e5b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "appid_filter" not in cols:
        op.add_column("alert_rules", sa.Column("appid_filter", JSONB(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "appid_filter" in cols:
        op.drop_column("alert_rules", "appid_filter")
