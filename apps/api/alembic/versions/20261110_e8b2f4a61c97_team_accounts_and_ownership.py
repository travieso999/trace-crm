"""team accounts and opportunity ownership

Multi-user support: usernames, per-user permission adjustments and soft deletion for users; who created, last
updated and assigned each opportunity. Additive only: no rows are deleted and existing owners are kept. The new
"created by" / "last updated by" columns are filled from the activity history that already exists.

Revision ID: e8b2f4a61c97
Revises: d7a1e5c30b48
Create Date: 2026-11-10 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e8b2f4a61c97'
down_revision: str | None = 'd7a1e5c30b48'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('username', sa.String(length=60), nullable=True))
    op.add_column('users', sa.Column('permission_overrides', postgresql.JSONB(astext_type=sa.Text()),
                                     server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('users', sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('uq_users_username_lower', 'users', [sa.text('lower(username)')], unique=True)

    op.add_column('crm_opportunities', sa.Column('owner_assigned_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('crm_opportunities', sa.Column('owner_assigned_by_id', sa.UUID(), nullable=True))
    op.add_column('crm_opportunities', sa.Column('created_by_id', sa.UUID(), nullable=True))
    op.add_column('crm_opportunities', sa.Column('updated_by_id', sa.UUID(), nullable=True))
    op.add_column('crm_opportunities', sa.Column('last_edited_at', sa.DateTime(timezone=True), nullable=True))
    for col in ('owner_assigned_by_id', 'created_by_id', 'updated_by_id'):
        op.create_foreign_key(op.f(f'fk_crm_opportunities_{col}_users'), 'crm_opportunities', 'users', [col], ['id'],
                              ondelete='SET NULL')
    op.create_index(op.f('ix_crm_opportunities_owner_id'), 'crm_opportunities', ['owner_id'], unique=False)

    # Backfill from history: the person who put it on the board (else its current owner, who was the creator),
    # and the last person who did anything to it.
    op.execute("""
        UPDATE crm_opportunities o SET created_by_id = COALESCE(
            (SELECT a.user_id FROM crm_activities a
              WHERE a.contact_id = o.contact_id AND a.type = 'opportunity_created' AND a.user_id IS NOT NULL
              ORDER BY a.created_at LIMIT 1),
            o.owner_id)
    """)
    op.execute("""
        UPDATE crm_opportunities o SET updated_by_id = last.user_id, last_edited_at = last.created_at
          FROM (SELECT DISTINCT ON (contact_id) contact_id, user_id, created_at FROM crm_activities
                 WHERE user_id IS NOT NULL ORDER BY contact_id, created_at DESC) last
         WHERE last.contact_id = o.contact_id
    """)


def downgrade() -> None:
    op.drop_index(op.f('ix_crm_opportunities_owner_id'), table_name='crm_opportunities')
    for col in ('owner_assigned_by_id', 'created_by_id', 'updated_by_id'):
        op.drop_constraint(op.f(f'fk_crm_opportunities_{col}_users'), 'crm_opportunities', type_='foreignkey')
    for col in ('last_edited_at', 'updated_by_id', 'created_by_id', 'owner_assigned_by_id', 'owner_assigned_at'):
        op.drop_column('crm_opportunities', col)
    op.drop_index('uq_users_username_lower', table_name='users')
    for col in ('deleted_at', 'password_changed_at', 'permission_overrides', 'username'):
        op.drop_column('users', col)
