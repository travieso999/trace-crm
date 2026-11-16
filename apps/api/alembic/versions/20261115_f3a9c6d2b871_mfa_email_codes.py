"""two-step verification by emailed code, as an alternative to an authenticator app

Additive only: three new columns. Existing two-step users keep the authenticator app ('totp').

Revision ID: f3a9c6d2b871
Revises: 5ca204ddd0e5
Create Date: 2026-11-15 17:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'f3a9c6d2b871'
down_revision: str | None = '5ca204ddd0e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mfa_method', sa.String(length=10), server_default='totp', nullable=False))
    op.add_column('users', sa.Column('email_code_hash', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('email_code_expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'email_code_expires_at')
    op.drop_column('users', 'email_code_hash')
    op.drop_column('users', 'mfa_method')
