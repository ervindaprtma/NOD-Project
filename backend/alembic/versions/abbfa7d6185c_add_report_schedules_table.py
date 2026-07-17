"""add report_schedules table (idempotent)

The model declared this table but no revision ever created it, so a fresh database
came up without it while existing ones already had it. Guarded so it is a no-op
where the table is already present.

Revision ID: abbfa7d6185c
Revises: 7f9945c67957
Create Date: 2026-07-17 05:02:36.539044
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'abbfa7d6185c'
down_revision: Union[str, None] = '7f9945c67957'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("report_schedules"):
        return
    op.create_table('report_schedules',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('report_type', sa.String(length=20), nullable=False),
    sa.Column('output_format', sa.String(length=10), nullable=False),
    sa.Column('cron_expression', sa.String(length=50), nullable=False),
    sa.Column('sites', sa.Text(), nullable=True),
    sa.Column('sections', sa.Text(), nullable=True),
    sa.Column('channels', sa.Text(), nullable=True),
    sa.Column('recipient_email', sa.String(length=255), nullable=True),
    sa.Column('recipient_phone', sa.String(length=50), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('report_schedules')
