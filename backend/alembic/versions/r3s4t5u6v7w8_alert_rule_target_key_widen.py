"""Widen alert_rules.target_key 64 → 255 for multi-interface iface rules (comma list of if_names).

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "r3s4t5u6v7w8"
down_revision = "q2r3s4t5u6v7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("alert_rules", "target_key",
                    existing_type=sa.String(length=64), type_=sa.String(length=255),
                    existing_nullable=True)


def downgrade() -> None:
    op.alter_column("alert_rules", "target_key",
                    existing_type=sa.String(length=255), type_=sa.String(length=64),
                    existing_nullable=True)
