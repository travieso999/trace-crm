"""smtp mailboxes

Revision ID: 5cbac32cf346
Revises: 10ed919f7ab4
Create Date: 2026-10-27 17:10:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = '5cbac32cf346'
down_revision: str | None = '10ed919f7ab4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('email_accounts', sa.Column('server', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('email_accounts', sa.Column('password_enc', sa.Text(), nullable=True))
    op.drop_constraint(op.f('ck_email_accounts_provider'), 'email_accounts', type_='check')
    op.create_check_constraint(op.f('ck_email_accounts_provider'), 'email_accounts',
                               "provider in ('gmail', 'microsoft', 'sandbox', 'smtp')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_email_accounts_provider'), 'email_accounts', type_='check')
    # fails while smtp mailboxes exist (they keep sent history); disconnect and remove them by hand first
    op.create_check_constraint(op.f('ck_email_accounts_provider'), 'email_accounts',
                               "provider in ('gmail', 'microsoft', 'sandbox')")
    op.drop_column('email_accounts', 'password_enc')
    op.drop_column('email_accounts', 'server')
