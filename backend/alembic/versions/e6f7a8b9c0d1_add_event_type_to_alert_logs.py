"""add event_type to alert_logs: session/reboot events join history

Revision ID: e6f7a8b9c0d1
Revises: c4d5e6f7a8b9
Create Date: 2026-08-10 11:30:00.000000

Threshold rules (single/composite) leave event_type NULL and keep deriving
Firing/Resolved from resolved_at. Point events (VPN connect/disconnect, device
reboot) tag their own name so Alert History labels them honestly instead of
mislabelling a connect as a "firing". Existing rows are all threshold rows, so
NULL is the correct backfill — no data migration needed.
"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by alembic.
revision = 'e6f7a8b9c0d1'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('alert_logs', sa.Column('event_type', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('alert_logs', 'event_type')
