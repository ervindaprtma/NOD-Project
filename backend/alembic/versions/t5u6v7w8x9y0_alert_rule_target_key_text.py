"""alert_rules.target_key 255 → TEXT: an iface rule's comma list of if_names overflows 255
once ~25 ports are picked (the API caps it at 8000).

Revision ID: t5u6v7w8x9y0
Revises: s4t5u6v7w8x9
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

revision = "t5u6v7w8x9y0"
down_revision = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("alert_rules", "target_key",
                    existing_type=sa.String(length=255), type_=sa.Text(),
                    existing_nullable=True)


def downgrade() -> None:
    op.alter_column("alert_rules", "target_key",
                    existing_type=sa.Text(), type_=sa.String(length=255),
                    existing_nullable=True)
