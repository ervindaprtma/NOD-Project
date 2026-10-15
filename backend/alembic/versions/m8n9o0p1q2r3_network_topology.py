"""network topology: nodes, links, device_overrides, icon_library (plan v3 §4.3).

Config tables for the Network Devices topology map + label overrides. No
telemetry stored here — live node/link state and traffic are read from
OpenSearch at request time.

- icon_library:    uploaded .png/.svg topology icons (superadmin)
- topology_nodes:  devices + support anchors; support=true → source_ip NULL
- topology_links:  device↔device links; link_type is an admin label
- device_overrides: per-source_ip display_name + iface alias/description

Revision ID: m8n9o0p1q2r3
Revises: l7m8n9o0p1q2
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "m8n9o0p1q2r3"
down_revision = "l7m8n9o0p1q2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "icon_library",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("name", name="uq_icon_library_name"),
    )
    op.create_table(
        "topology_nodes",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
        sa.Column("support", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("icon_kind", sa.String(length=16), server_default="sw", nullable=False),
        sa.Column("icon_id", UUID(as_uuid=False),
                  sa.ForeignKey("icon_library.id", ondelete="SET NULL"), nullable=True),
        sa.Column("x", sa.Integer(), server_default="0", nullable=False),
        sa.Column("y", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("source_ip", name="uq_topology_nodes_source_ip"),
    )
    op.create_table(
        "topology_links",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("a_node_id", UUID(as_uuid=False),
                  sa.ForeignKey("topology_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("a_if_name", sa.String(length=255), nullable=True),
        sa.Column("b_node_id", UUID(as_uuid=False),
                  sa.ForeignKey("topology_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("b_if_name", sa.String(length=255), nullable=True),
        sa.Column("link_type", sa.String(length=16), server_default="lan", nullable=False),
    )
    op.create_index("ix_topology_links_a_node_id", "topology_links", ["a_node_id"])
    op.create_index("ix_topology_links_b_node_id", "topology_links", ["b_node_id"])
    op.create_table(
        "device_overrides",
        sa.Column("source_ip", sa.String(length=45), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("iface_overrides", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("device_overrides")
    op.drop_index("ix_topology_links_b_node_id", table_name="topology_links")
    op.drop_index("ix_topology_links_a_node_id", table_name="topology_links")
    op.drop_table("topology_links")
    op.drop_table("topology_nodes")
    op.drop_table("icon_library")
