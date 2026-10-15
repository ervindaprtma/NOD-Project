"""report_jobs.devices: optional device scope for R-11 (network device interface report).

Stores the list of device source_ips a report is scoped to. NULL/empty = all devices
in the selected sites.

Revision ID: n9o0p1q2r3s4
Revises: m8n9o0p1q2r3
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "n9o0p1q2r3s4"
down_revision = "m8n9o0p1q2r3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_jobs",
        sa.Column("devices", JSONB(), nullable=True,
                  comment="R-11 only: device source_ips to include; NULL/empty = all in the selected sites"),
    )


def downgrade() -> None:
    op.drop_column("report_jobs", "devices")
