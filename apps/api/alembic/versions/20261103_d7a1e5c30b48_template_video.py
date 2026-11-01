"""template video

Templates can carry a video, its thumbnail and button text, like a campaign email. Additive only: nothing is dropped or
overwritten.

Revision ID: d7a1e5c30b48
Revises: c4e92b7d1a35
Create Date: 2026-11-03 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'd7a1e5c30b48'
down_revision: str | None = 'c4e92b7d1a35'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('email_templates', sa.Column('video_asset_id', sa.UUID(), nullable=True))
    op.add_column('email_templates', sa.Column('video_preview', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('email_templates', sa.Column('video_attach', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('email_templates', sa.Column('video_thumbnail_id', sa.UUID(), nullable=True))
    op.add_column('email_templates', sa.Column('video_cta_text', sa.String(length=80), nullable=True))
    op.create_foreign_key(op.f('fk_email_templates_video_asset_id_video_assets'), 'email_templates', 'video_assets',
                          ['video_asset_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(op.f('fk_email_templates_video_thumbnail_id_email_images'), 'email_templates', 'email_images',
                          ['video_thumbnail_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_email_templates_video_thumbnail_id_email_images'), 'email_templates', type_='foreignkey')
    op.drop_constraint(op.f('fk_email_templates_video_asset_id_video_assets'), 'email_templates', type_='foreignkey')
    op.drop_column('email_templates', 'video_cta_text')
    op.drop_column('email_templates', 'video_thumbnail_id')
    op.drop_column('email_templates', 'video_attach')
    op.drop_column('email_templates', 'video_preview')
    op.drop_column('email_templates', 'video_asset_id')
