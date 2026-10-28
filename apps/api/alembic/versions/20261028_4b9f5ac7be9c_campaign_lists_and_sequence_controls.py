"""campaign lists and sequence controls

Imported lists (import_batches + members), the lists a campaign used, campaign details and audience mode,
named/draft/paused/scheduled sequence steps, and a "paused" state for one prospect in a campaign.
Additive only: existing rows keep working with the defaults below.

Revision ID: 4b9f5ac7be9c
Revises: 5cbac32cf346
Create Date: 2026-10-28 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = '4b9f5ac7be9c'
down_revision: str | None = '5cbac32cf346'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CC = "status in ('active', 'replied', 'completed', 'unsubscribed', 'bounced', 'stopped', 'removed', 'error')"
NEW_CC = ("status in ('active', 'paused', 'replied', 'completed', 'unsubscribed', 'bounced', 'stopped', 'removed', "
          "'error')")


def upgrade() -> None:
    op.create_table(
        'import_batches',
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('original_filename', sa.String(length=300), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('file_format', sa.String(length=10), nullable=True),
        sa.Column('total_rows', sa.Integer(), nullable=False),
        sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_import_batches_created_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_import_batches')),
    )
    op.create_index(op.f('ix_import_batches_sha256'), 'import_batches', ['sha256'], unique=False)
    op.create_table(
        'import_batch_members',
        sa.Column('batch_id', sa.UUID(), nullable=False),
        sa.Column('contact_id', sa.UUID(), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('row_number', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'],
                                name=op.f('fk_import_batch_members_batch_id_import_batches'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'],
                                name=op.f('fk_import_batch_members_contact_id_contacts'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('batch_id', 'contact_id', name=op.f('pk_import_batch_members')),
    )
    op.create_index(op.f('ix_import_batch_members_contact_id'), 'import_batch_members', ['contact_id'], unique=False)
    op.create_table(
        'campaign_import_batches',
        sa.Column('campaign_id', sa.UUID(), nullable=False),
        sa.Column('batch_id', sa.UUID(), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'],
                                name=op.f('fk_campaign_import_batches_batch_id_import_batches'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name=op.f('fk_campaign_import_batches_campaign_id_campaigns'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', 'batch_id', name=op.f('pk_campaign_import_batches')),
    )
    op.create_index(op.f('ix_campaign_import_batches_batch_id'), 'campaign_import_batches', ['batch_id'],
                    unique=False)

    op.add_column('campaigns', sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('campaigns', sa.Column('campaign_type', sa.String(length=60), nullable=True))
    op.add_column('campaigns', sa.Column('target_location', sa.String(length=120), nullable=True))
    op.add_column('campaigns', sa.Column('industry', sa.String(length=120), nullable=True))
    op.add_column('campaigns', sa.Column('audience_mode', sa.String(length=10), nullable=False,
                                         server_default='fixed'))
    op.add_column('campaigns', sa.Column('audience_filter', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                                         server_default=sa.text("'{}'::jsonb")))
    op.alter_column('campaigns', 'audience_mode', server_default=None)
    op.alter_column('campaigns', 'audience_filter', server_default=None)
    op.create_check_constraint(op.f('ck_campaigns_audience_mode'), 'campaigns', "audience_mode in ('fixed', 'dynamic')")

    op.add_column('sequence_steps', sa.Column('name', sa.String(length=120), nullable=True))
    op.add_column('sequence_steps', sa.Column('status', sa.String(length=10), nullable=False, server_default='active'))
    op.add_column('sequence_steps', sa.Column('send_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('sequence_steps', 'status', server_default=None)
    op.create_check_constraint(op.f('ck_sequence_steps_status'), 'sequence_steps',
                               "status in ('draft', 'active', 'paused')")

    op.add_column('campaign_contacts', sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(op.f('ck_campaign_contacts_status'), 'campaign_contacts', type_='check')
    op.create_check_constraint(op.f('ck_campaign_contacts_status'), 'campaign_contacts', NEW_CC)


def downgrade() -> None:
    op.execute("UPDATE campaign_contacts SET status = 'stopped', stop_reason = 'paused_before_downgrade' "
               "WHERE status = 'paused'")
    op.drop_constraint(op.f('ck_campaign_contacts_status'), 'campaign_contacts', type_='check')
    op.create_check_constraint(op.f('ck_campaign_contacts_status'), 'campaign_contacts', OLD_CC)
    op.drop_column('campaign_contacts', 'paused_at')
    op.drop_constraint(op.f('ck_sequence_steps_status'), 'sequence_steps', type_='check')
    op.drop_column('sequence_steps', 'send_at')
    op.drop_column('sequence_steps', 'status')
    op.drop_column('sequence_steps', 'name')
    op.drop_constraint(op.f('ck_campaigns_audience_mode'), 'campaigns', type_='check')
    op.drop_column('campaigns', 'audience_filter')
    op.drop_column('campaigns', 'audience_mode')
    op.drop_column('campaigns', 'industry')
    op.drop_column('campaigns', 'target_location')
    op.drop_column('campaigns', 'campaign_type')
    op.drop_column('campaigns', 'paused_at')
    op.drop_index(op.f('ix_campaign_import_batches_batch_id'), table_name='campaign_import_batches')
    op.drop_table('campaign_import_batches')
    op.drop_index(op.f('ix_import_batch_members_contact_id'), table_name='import_batch_members')
    op.drop_table('import_batch_members')
    op.drop_index(op.f('ix_import_batches_sha256'), table_name='import_batches')
    op.drop_table('import_batches')
