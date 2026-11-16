"""Make audit_events append-only: UPDATE, DELETE and TRUNCATE raise an error.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION cf360_audit_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
                  FOR EACH ROW EXECUTE FUNCTION cf360_audit_append_only();""")
    op.execute("""CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
                  FOR EACH STATEMENT EXECUTE FUNCTION cf360_audit_append_only();""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS cf360_audit_append_only()")
