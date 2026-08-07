"""create system_logs table (Logging System)

Revision ID: b3c4d5e6f7a8
Revises: f5a6b7c8d9e0
Create Date: 2026-08-07 07:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by alembic.
revision = 'b3c4d5e6f7a8'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'system_logs',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('level', sa.String(length=10), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('event', sa.String(length=60), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('username', sa.String(length=150), nullable=True),
        sa.Column('user_id', UUID(as_uuid=False), nullable=True),
        sa.Column('source_ip', sa.String(length=45), nullable=True),
        sa.Column('trace_id', sa.String(length=64), nullable=True),
        sa.Column('rule_id', UUID(as_uuid=False), nullable=True),
        sa.Column('method', sa.String(length=8), nullable=True),
        sa.Column('path', sa.String(length=255), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('details', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_system_logs_ts', 'system_logs', ['ts'])
    op.create_index('ix_system_logs_level', 'system_logs', ['level'])
    op.create_index('ix_system_logs_category', 'system_logs', ['category'])
    op.create_index('ix_system_logs_source', 'system_logs', ['source'])
    op.create_index('ix_system_logs_event', 'system_logs', ['event'])
    op.create_index('ix_system_logs_username', 'system_logs', ['username'])
    op.create_index('ix_system_logs_trace_id', 'system_logs', ['trace_id'])
    # Default tabbed view: filter by level, newest first.
    op.create_index('ix_system_logs_level_ts', 'system_logs', ['level', sa.text('ts DESC')])


def downgrade() -> None:
    op.drop_index('ix_system_logs_level_ts', table_name='system_logs')
    op.drop_index('ix_system_logs_trace_id', table_name='system_logs')
    op.drop_index('ix_system_logs_username', table_name='system_logs')
    op.drop_index('ix_system_logs_event', table_name='system_logs')
    op.drop_index('ix_system_logs_source', table_name='system_logs')
    op.drop_index('ix_system_logs_category', table_name='system_logs')
    op.drop_index('ix_system_logs_level', table_name='system_logs')
    op.drop_index('ix_system_logs_ts', table_name='system_logs')
    op.drop_table('system_logs')
