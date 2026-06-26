"""add sections column to report_jobs

Revision ID: f1a2b3c4d5e6
Revises: d5e6f7a8b9c0
Create Date: 2026-06-27 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_jobs",
        sa.Column(
            "sections",
            postgresql.JSONB,
            nullable=True,
            comment="List of report sections to include; None = all",
        ),
    )


def downgrade() -> None:
    op.drop_column("report_jobs", "sections")