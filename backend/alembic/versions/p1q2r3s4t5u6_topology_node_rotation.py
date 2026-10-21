"""topology_nodes.rotation: degrees clockwise for text annotation nodes (0 = upright).

Revision ID: p1q2r3s4t5u6
Revises: o0p1q2r3s4t5
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "p1q2r3s4t5u6"
down_revision = "o0p1q2r3s4t5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "topology_nodes",
        sa.Column("rotation", sa.Integer(), nullable=False, server_default="0",
                  comment="Degrees clockwise (text annotation nodes); 0 = upright"),
    )


def downgrade() -> None:
    op.drop_column("topology_nodes", "rotation")
