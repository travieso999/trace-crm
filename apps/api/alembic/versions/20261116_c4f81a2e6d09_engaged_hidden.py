"""engaged prospects: hide a prospect until they engage again

Additive: one new table. Also counts as human the demo-link clicks flagged as scanners that were followed by a
visit on trace-studio.tech within 30 minutes (the click that brought them), as the API now does when visits arrive.

Revision ID: c4f81a2e6d09
Revises: b9d4e2a7c315
Create Date: 2026-11-16 20:30:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c4f81a2e6d09'
down_revision: str | None = 'b9d4e2a7c315'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('engaged_hidden',
    sa.Column('contact_id', sa.UUID(), nullable=False),
    sa.Column('hidden_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('hidden_by_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_engaged_hidden_contact_id_contacts'),
                            ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['hidden_by_id'], ['users.id'], name=op.f('fk_engaged_hidden_hidden_by_id_users'),
                            ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('contact_id', name=op.f('pk_engaged_hidden'))
    )
    op.execute(
        "UPDATE tracking_events e SET verified_human = true FROM tracked_links l "
        "WHERE e.link_id = l.id AND l.kind = 'demo' AND e.type = 'click' AND e.suspected_bot "
        "AND NOT e.verified_human AND NOT (e.bot_reasons @> '[\"crm_user\"]'::jsonb) "
        "AND EXISTS (SELECT 1 FROM site_events s WHERE s.contact_id = l.contact_id "
        "AND s.received_at BETWEEN e.occurred_at AND e.occurred_at + interval '30 minutes')"
    )


def downgrade() -> None:
    op.drop_table('engaged_hidden')
