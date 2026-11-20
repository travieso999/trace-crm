"""cross-campaign cooldown: an email waits until N days after another campaign emailed the prospect

Additive: one column with a server default, so existing campaigns get the 14-day cooldown straight away. Also an
index for the check the sender runs before every email (a prospect's recent sends).

Revision ID: d2b7f0a94c13
Revises: c4f81a2e6d09
Create Date: 2026-11-19 20:30:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd2b7f0a94c13'
down_revision: str | None = 'c4f81a2e6d09'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('campaigns', sa.Column('cross_campaign_cooldown_days', sa.Integer(), server_default='14',
                                         nullable=False))
    op.create_index('ix_sent_messages_contact_sent', 'sent_messages', ['contact_id', 'sent_at'])


def downgrade() -> None:
    op.drop_index('ix_sent_messages_contact_sent', table_name='sent_messages')
    op.drop_column('campaigns', 'cross_campaign_cooldown_days')
