"""Topology: per-link curve control-point offset.

topology_links.curve_dx / curve_dy: offset (canvas px) of the quadratic Bézier
control point from the link midpoint. (0, 0) = straight line. Lets a superadmin
bend each cable by dragging its curve handle; shared like node positions.

Revision ID: s4t5u6v7w8x9
Revises: r3s4t5u6v7w8
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = "s4t5u6v7w8x9"
down_revision = "r3s4t5u6v7w8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("topology_links",
                  sa.Column("curve_dx", sa.Float(), nullable=False, server_default="0"))
    op.add_column("topology_links",
                  sa.Column("curve_dy", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("topology_links", "curve_dy")
    op.drop_column("topology_links", "curve_dx")
