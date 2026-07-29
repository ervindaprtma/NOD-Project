"""add report_jobs.sla_thresholds for R-04 per-link-type SLA ceilings

R-04 lets the operator supply SLA thresholds (WAN/MPLS × latency/jitter/packet_loss);
the async report job stores them so the background generator can mark each link
Met/Breached. Nullable JSONB — every other report type leaves it null.
Idempotent (inspector-guarded) to match this project's migration style.

Revision ID: b7e2c9d1a4f8
Revises: f4d5e6a7b8c9
Create Date: 2026-07-29 03:24:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b7e2c9d1a4f8"
down_revision: Union[str, None] = "f4d5e6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("report_jobs")}
    if "sla_thresholds" not in existing:
        op.add_column("report_jobs", sa.Column("sla_thresholds", JSONB, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("report_jobs")}
    if "sla_thresholds" in existing:
        op.drop_column("report_jobs", "sla_thresholds")
