"""alert_rules.telegram_thread_id: Telegram forum topic (message_thread_id) per rule.

NULL = send to the group's General topic (today's behaviour). Set per rule (pre-filled
from the alert template, editable in the rule form) so each category lands in its own topic.

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_rules",
        sa.Column("telegram_thread_id", sa.Integer(), nullable=True,
                  comment="Telegram forum topic (message_thread_id); NULL = General topic"),
    )


def downgrade() -> None:
    op.drop_column("alert_rules", "telegram_thread_id")
