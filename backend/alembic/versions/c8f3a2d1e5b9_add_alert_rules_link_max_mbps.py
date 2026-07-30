"""add alert_rules.link_max_mbps for interface %-of-link-max threshold

interface_stats alerts can target an absolute Mbps threshold or a % of a link max the
operator enters. The % mode stores the resulting Mbps in threshold_value; link_max_mbps
remembers the entered max so the UI can show the % again on edit. Nullable Float —
absolute-Mbps rules leave it null. Idempotent (inspector-guarded), project style.

Revision ID: c8f3a2d1e5b9
Revises: b7e2c9d1a4f8
Create Date: 2026-07-30 08:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8f3a2d1e5b9"
down_revision: Union[str, None] = "b7e2c9d1a4f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "link_max_mbps" not in existing:
        op.add_column("alert_rules", sa.Column("link_max_mbps", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("alert_rules")}
    if "link_max_mbps" in existing:
        op.drop_column("alert_rules", "link_max_mbps")
