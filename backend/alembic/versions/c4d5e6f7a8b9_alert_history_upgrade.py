"""alert history upgrade: event_code, sent_payloads, rule_id SET NULL

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-07 09:00:00.000000

"""
import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def _event_code(rule_id, fired_at, metric_value, snapshot) -> str:
    driver = (snapshot or {}).get("driver") if isinstance(snapshot, dict) else None
    s = (driver or {}).get("metric_field") if isinstance(driver, dict) else None
    if s is None and isinstance(snapshot, dict):
        s = snapshot.get("metric_field")
    basis = json.dumps({"r": str(rule_id), "t": str(fired_at), "v": metric_value, "s": s},
                       sort_keys=True, default=str)
    h = hashlib.sha256(basis.encode()).hexdigest()[:8]
    day = fired_at.strftime("%Y%m%d") if hasattr(fired_at, "strftime") else str(fired_at)[:10].replace("-", "")
    return f"AH-{day}-{h}"


def upgrade() -> None:
    op.add_column('alert_logs', sa.Column('event_code', sa.String(length=24), nullable=True))
    op.add_column('alert_logs', sa.Column(
        'sent_payloads', JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_index('ix_alert_logs_event_code', 'alert_logs', ['event_code'])

    # rule_id: CASCADE → SET NULL so deleting a rule keeps its history.
    op.alter_column('alert_logs', 'rule_id', existing_type=UUID(as_uuid=False), nullable=True)
    op.drop_constraint('alert_logs_rule_id_fkey', 'alert_logs', type_='foreignkey')
    op.create_foreign_key(
        'alert_logs_rule_id_fkey', 'alert_logs', 'alert_rules',
        ['rule_id'], ['id'], ondelete='SET NULL',
    )

    # Backfill event_code for existing rows (same hash the engine uses at runtime).
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, rule_id, fired_at, metric_value_at_firing, rule_snapshot FROM alert_logs"
    )).fetchall()
    for r in rows:
        code = _event_code(r[1], r[2], r[3], r[4])
        bind.execute(
            sa.text("UPDATE alert_logs SET event_code = :c WHERE id = :i"),
            {"c": code, "i": r[0]},
        )


def downgrade() -> None:
    op.drop_constraint('alert_logs_rule_id_fkey', 'alert_logs', type_='foreignkey')
    op.create_foreign_key(
        'alert_logs_rule_id_fkey', 'alert_logs', 'alert_rules',
        ['rule_id'], ['id'], ondelete='CASCADE',
    )
    op.alter_column('alert_logs', 'rule_id', existing_type=UUID(as_uuid=False), nullable=False)
    op.drop_index('ix_alert_logs_event_code', table_name='alert_logs')
    op.drop_column('alert_logs', 'sent_payloads')
    op.drop_column('alert_logs', 'event_code')
