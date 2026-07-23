"""fix Default Alert line_template Jinja slice syntax

The seeded "Default Alert" line_template used `{{ rule.severity|upper[:3] }}`, which
Jinja parses as subscripting the filter name -> "expected token 'end of print
statement', got '['". The slice must wrap the filtered value: `(rule.severity|upper)[:3]`.
The seeder skips when rows exist, so existing DBs keep the broken row — this backfills it.

Guarded by a LIKE on the broken fragment so a user-edited template is never touched.

Revision ID: f4d5e6a7b8c9
Revises: e3c4d5f6a7b8
Create Date: 2026-07-21 02:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4d5e6a7b8c9"
down_revision: Union[str, None] = "e3c4d5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BROKEN = "[{{ rule.severity|upper[:3] }}] {{ rule.name }}: {{ metric_value|round(2) }} ({{ rule.condition }} {{ rule.threshold_value }})"
_FIXED = "[{{ (rule.severity|upper)[:3] }}] {{ rule.name }}: {{ metric_value|round(2) }} ({{ rule.condition }} {{ rule.threshold_value }})"


def upgrade() -> None:
    # Exact-match the broken seeded value only — never touch a user-edited template.
    op.execute(
        sa.text(
            "UPDATE notification_templates SET line_template = :fixed "
            "WHERE line_template = :broken"
        ).bindparams(fixed=_FIXED, broken=_BROKEN)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE notification_templates SET line_template = :broken "
            "WHERE line_template = :fixed"
        ).bindparams(broken=_BROKEN, fixed=_FIXED)
    )
