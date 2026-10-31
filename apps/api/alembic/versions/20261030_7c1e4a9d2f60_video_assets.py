"""video assets

A reusable library of uploaded MP4s (stored in chunks) and, on each sequence step, which video it uses and how: as a
clickable preview, a real attachment, or both. Additive only: existing steps keep working with no video.

Revision ID: 7c1e4a9d2f60
Revises: 4b9f5ac7be9c
Create Date: 2026-10-30 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '7c1e4a9d2f60'
down_revision: str | None = '4b9f5ac7be9c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'video_assets',
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('link_url', sa.Text(), nullable=True),
        sa.Column('thumbnail', sa.LargeBinary(), nullable=True),
        sa.Column('thumbnail_type', sa.String(length=20), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('uploading', 'ready')", name=op.f('ck_video_assets_status')),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_video_assets_created_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_video_assets')),
    )
    op.create_index(op.f('ix_video_assets_sha256'), 'video_assets', ['sha256'], unique=False)
    op.create_table(
        'video_asset_chunks',
        sa.Column('asset_id', sa.UUID(), nullable=False),
        sa.Column('index', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['video_assets.id'], name=op.f('fk_video_asset_chunks_asset_id_video_assets'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('asset_id', 'index', name=op.f('pk_video_asset_chunks')),
    )
    op.add_column('sequence_steps', sa.Column('video_asset_id', sa.UUID(), nullable=True))
    op.add_column('sequence_steps', sa.Column('video_preview', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('sequence_steps', sa.Column('video_attach', sa.Boolean(), server_default='false', nullable=False))
    op.create_index(op.f('ix_sequence_steps_video_asset_id'), 'sequence_steps', ['video_asset_id'], unique=False)
    op.create_foreign_key(op.f('fk_sequence_steps_video_asset_id_video_assets'), 'sequence_steps', 'video_assets',
                          ['video_asset_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_sequence_steps_video_asset_id_video_assets'), 'sequence_steps', type_='foreignkey')
    op.drop_index(op.f('ix_sequence_steps_video_asset_id'), table_name='sequence_steps')
    op.drop_column('sequence_steps', 'video_attach')
    op.drop_column('sequence_steps', 'video_preview')
    op.drop_column('sequence_steps', 'video_asset_id')
    op.drop_table('video_asset_chunks')
    op.drop_index(op.f('ix_video_assets_sha256'), table_name='video_assets')
    op.drop_table('video_assets')
