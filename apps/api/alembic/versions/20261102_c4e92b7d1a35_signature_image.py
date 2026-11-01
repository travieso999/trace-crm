"""signature image

A designed signature image per mailbox, and where clicking it goes. Additive only: nothing is dropped or overwritten.

Revision ID: c4e92b7d1a35
Revises: a3d81f6c0b27
Create Date: 2026-11-02 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c4e92b7d1a35'
down_revision: str | None = 'a3d81f6c0b27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('email_accounts', sa.Column('signature_image_id', sa.UUID(), nullable=True))
    op.add_column('email_accounts', sa.Column('signature_link', sa.Text(), nullable=True))
    op.create_foreign_key(op.f('fk_email_accounts_signature_image_id_email_images'), 'email_accounts', 'email_images',
                          ['signature_image_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_email_accounts_signature_image_id_email_images'), 'email_accounts', type_='foreignkey')
    op.drop_column('email_accounts', 'signature_link')
    op.drop_column('email_accounts', 'signature_image_id')
