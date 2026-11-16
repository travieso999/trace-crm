"""website tracking: visitors and events forwarded by trace-studio.tech

Additive: two new tables. Also adds https://trace-studio.tech/demo to Settings → Tracking → Demo links when that list
was saved before (the default list already includes it), so clicks on the campaign video page count as demo clicks.

Revision ID: b9d4e2a7c315
Revises: a7e3c9d14b62
Create Date: 2026-11-16 12:10:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b9d4e2a7c315'
down_revision: str | None = 'a7e3c9d14b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_PAGE = "https://trace-studio.tech/demo"


def upgrade() -> None:
    op.create_table('site_visitors',
    sa.Column('vid', sa.String(length=64), nullable=False),
    sa.Column('contact_id', sa.UUID(), nullable=True),
    sa.Column('rid', sa.String(length=100), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('phone', sa.String(length=60), nullable=True),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('region', sa.String(length=120), nullable=True),
    sa.Column('country', sa.String(length=8), nullable=True),
    sa.Column('device', sa.String(length=80), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('event_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_site_visitors_contact_id_contacts'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_site_visitors')),
    sa.UniqueConstraint('vid', name=op.f('uq_site_visitors_vid'))
    )
    op.create_index(op.f('ix_site_visitors_contact_id'), 'site_visitors', ['contact_id'], unique=False)
    op.create_index(op.f('ix_site_visitors_email'), 'site_visitors', ['email'], unique=False)
    op.create_index(op.f('ix_site_visitors_last_seen_at'), 'site_visitors', ['last_seen_at'], unique=False)
    op.create_table('site_events',
    sa.Column('dedupe_key', sa.String(length=64), nullable=False),
    sa.Column('visitor_id', sa.UUID(), nullable=False),
    sa.Column('sid', sa.String(length=64), nullable=False),
    sa.Column('rid', sa.String(length=100), nullable=True),
    sa.Column('contact_id', sa.UUID(), nullable=True),
    sa.Column('campaign_id', sa.UUID(), nullable=True),
    sa.Column('type', sa.String(length=40), nullable=False),
    sa.Column('site', sa.String(length=80), nullable=True),
    sa.Column('page', sa.String(length=300), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('region', sa.String(length=120), nullable=True),
    sa.Column('country', sa.String(length=8), nullable=True),
    sa.Column('device', sa.String(length=80), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_site_events_campaign_id_campaigns'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], name=op.f('fk_site_events_contact_id_contacts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['visitor_id'], ['site_visitors.id'], name=op.f('fk_site_events_visitor_id_site_visitors'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_site_events')),
    sa.UniqueConstraint('dedupe_key', name=op.f('uq_site_events_dedupe_key'))
    )
    op.create_index(op.f('ix_site_events_campaign_id'), 'site_events', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_site_events_occurred_at'), 'site_events', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_site_events_sid'), 'site_events', ['sid'], unique=False)
    op.create_index('ix_site_events_contact_time', 'site_events', ['contact_id', 'occurred_at'], unique=False)
    op.create_index('ix_site_events_visitor_time', 'site_events', ['visitor_id', 'occurred_at'], unique=False)

    # a saved Demo links list doesn't pick up new defaults: add the campaign video page to it
    op.execute(sa.text(
        "UPDATE app_settings SET value = jsonb_set(value, '{demo_urls}', (value->'demo_urls') || to_jsonb(CAST(:u AS text))) "
        "WHERE key = 'tracking' AND jsonb_typeof(value->'demo_urls') = 'array' AND NOT (value->'demo_urls') ? :u"
    ).bindparams(u=DEMO_PAGE))


def downgrade() -> None:
    op.drop_index('ix_site_events_visitor_time', table_name='site_events')
    op.drop_index('ix_site_events_contact_time', table_name='site_events')
    op.drop_index(op.f('ix_site_events_sid'), table_name='site_events')
    op.drop_index(op.f('ix_site_events_occurred_at'), table_name='site_events')
    op.drop_index(op.f('ix_site_events_campaign_id'), table_name='site_events')
    op.drop_table('site_events')
    op.drop_index(op.f('ix_site_visitors_last_seen_at'), table_name='site_visitors')
    op.drop_index(op.f('ix_site_visitors_email'), table_name='site_visitors')
    op.drop_index(op.f('ix_site_visitors_contact_id'), table_name='site_visitors')
    op.drop_table('site_visitors')
