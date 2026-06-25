"""add source_ip to refresh_tokens

Revision ID: d5e6f7a8b9c0
Revises: c93622f9e19f
Create Date: 2026-06-25 10:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c93622f9e19f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens",
        sa.Column(
            "source_ip",
            sa.String(45),
            nullable=True,
            comment="IP address of the client that created this token (IPv4 or IPv6)",
        ),
    )


def downgrade() -> None:
    op.drop_column("refresh_tokens", "source_ip")
