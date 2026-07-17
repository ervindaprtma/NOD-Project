"""drop NOT NULL on report created_by so ondelete=SET NULL can fire

Both columns were declared ondelete="SET NULL" but NOT NULL, which is a
contradiction Postgres only surfaces at DELETE time: removing any user who had
ever run a report raised NotNullViolation from the cascade's UPDATE ... SET
created_by = NULL. Reproduced against the live DB before writing this.

Revision ID: b8d4c1e90a37
Revises: abbfa7d6185c
Create Date: 2026-07-17 09:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8d4c1e90a37"
down_revision: Union[str, None] = "abbfa7d6185c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("report_jobs", "report_schedules")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "created_by", existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    # Re-imposing NOT NULL fails on any row already orphaned by a user delete, so
    # drop those first — they are unreachable reports of a user who no longer exists.
    for table in _TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE created_by IS NULL"))
        op.alter_column(table, "created_by", existing_type=sa.UUID(), nullable=False)
