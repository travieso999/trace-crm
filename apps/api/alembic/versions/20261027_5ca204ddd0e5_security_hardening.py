"""security hardening: two-step verification, session idle timeout, append-only audit log

Additive only: new nullable columns and a trigger. No existing data is changed.

Revision ID: 5ca204ddd0e5
Revises: 10ed919f7ab4
Create Date: 2026-10-27 13:30:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '5ca204ddd0e5'
down_revision: str | None = '10ed919f7ab4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('totp_secret_enc', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('totp_last_step', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('recovery_code_hashes', postgresql.JSONB(astext_type=sa.Text()),
                                     server_default='[]', nullable=False))
    op.add_column('sessions', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    # The audit trail is append-only for the application: rows can't be edited, and can only be removed once they
    # are past the retention period (730 days). TRUNCATE still needs the table owner, as it always did.
    op.execute("""
        CREATE FUNCTION audit_logs_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'audit_logs is append-only';
            END IF;
            IF OLD.created_at > now() - interval '730 days' THEN
                RAISE EXCEPTION 'audit_logs rows are kept for 730 days';
            END IF;
            RETURN OLD;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_append_only()")
    op.drop_column('sessions', 'last_seen_at')
    op.drop_column('users', 'recovery_code_hashes')
    op.drop_column('users', 'totp_last_step')
    op.drop_column('users', 'totp_enabled_at')
    op.drop_column('users', 'totp_secret_enc')
