"""add table_interval column to report_jobs

Revision ID: g2h3i4j5k6l7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-28 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g2h3i4j5k6l7"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_jobs",
        sa.Column(
            "table_interval",
            sa.String(10),
            nullable=True,
            comment="R-09 only: interval for detail table rows (15m/30m/1h/2h/4h/6h/12h/24h)",
        ),
    )


def downgrade() -> None:
    op.drop_column("report_jobs", "table_interval")
