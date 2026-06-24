"""add sites column to report_jobs

Revision ID: c93622f9e19f
Revises: a1b2c3d4e5f6
Create Date: 2026-06-24 09:58:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c93622f9e19f"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "report_jobs",
        sa.Column("sites", postgresql.JSONB, nullable=True,
                  comment="List of site names to include in report"),
    )


def downgrade() -> None:
    op.drop_column("report_jobs", "sites")
