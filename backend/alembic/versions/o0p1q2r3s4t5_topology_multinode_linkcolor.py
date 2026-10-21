"""Topology: allow a device on the map more than once + per-link colour.

- Drop uq_topology_nodes_source_ip so the same tag.source can be placed as several
  nodes (live state keys by source_ip and colours every copy the same).
- topology_links.color: optional per-link stroke colour (hex, e.g. "#ef4444");
  NULL = the default muted stroke.

Revision ID: o0p1q2r3s4t5
Revises: n9o0p1q2r3s4
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "o0p1q2r3s4t5"
down_revision = "n9o0p1q2r3s4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_topology_nodes_source_ip", "topology_nodes", type_="unique")
    op.add_column(
        "topology_links",
        sa.Column("color", sa.String(length=16), nullable=True,
                  comment="Optional per-link stroke colour (hex); NULL = default muted stroke"),
    )


def downgrade() -> None:
    op.drop_column("topology_links", "color")
    # Re-adding the unique constraint fails if duplicates now exist; dedupe first in that case.
    op.create_unique_constraint("uq_topology_nodes_source_ip", "topology_nodes", ["source_ip"])
