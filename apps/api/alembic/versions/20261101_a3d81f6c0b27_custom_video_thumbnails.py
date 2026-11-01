"""custom video thumbnails

Processed email images with a public URL, a custom thumbnail and button text per sequence step. Thumbnails already
generated for videos are copied across and the old columns stay. Additive only: nothing is dropped or overwritten.

Revision ID: a3d81f6c0b27
Revises: 7c1e4a9d2f60
Create Date: 2026-11-01 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'a3d81f6c0b27'
down_revision: str | None = '7c1e4a9d2f60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'email_images',
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=20), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_email_images_created_by_users'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_email_images')),
    )
    op.add_column('sequence_steps', sa.Column('video_thumbnail_id', sa.UUID(), nullable=True))
    op.add_column('sequence_steps', sa.Column('video_cta_text', sa.String(length=80), nullable=True))
    op.create_foreign_key(op.f('fk_sequence_steps_video_thumbnail_id_email_images'), 'sequence_steps', 'email_images',
                          ['video_thumbnail_id'], ['id'], ondelete='SET NULL')
    op.add_column('video_assets', sa.Column('thumbnail_image_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_video_assets_thumbnail_image_id_email_images'), 'video_assets', 'email_images',
                          ['thumbnail_image_id'], ['id'], ondelete='SET NULL')
    # one new image row per video that already has a generated thumbnail; the image id is derived from the video's id
    op.execute("""
        INSERT INTO email_images (id, filename, content_type, size_bytes, data, created_by, created_at)
        SELECT md5('video-thumbnail:' || id::text)::uuid, filename, thumbnail_type, length(thumbnail), thumbnail, created_by, now()
        FROM video_assets WHERE thumbnail IS NOT NULL AND thumbnail_type IS NOT NULL
    """)
    op.execute("""
        UPDATE video_assets SET thumbnail_image_id = md5('video-thumbnail:' || id::text)::uuid
        WHERE thumbnail IS NOT NULL AND thumbnail_type IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_constraint(op.f('fk_video_assets_thumbnail_image_id_email_images'), 'video_assets', type_='foreignkey')
    op.drop_column('video_assets', 'thumbnail_image_id')
    op.drop_constraint(op.f('fk_sequence_steps_video_thumbnail_id_email_images'), 'sequence_steps', type_='foreignkey')
    op.drop_column('sequence_steps', 'video_cta_text')
    op.drop_column('sequence_steps', 'video_thumbnail_id')
    op.drop_table('email_images')
