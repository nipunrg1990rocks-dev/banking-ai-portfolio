"""Widen test result identifiers (generated case ids such as lifecycle edges exceed 40 characters) and the run environment.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("test_results", "case_id", type_=sa.String(80), existing_type=sa.String(40))
    op.alter_column("test_results", "scenario_id", type_=sa.String(60), existing_type=sa.String(20))
    op.alter_column("test_results", "project", type_=sa.String(40), existing_type=sa.String(30))
    op.alter_column("test_runs", "environment", type_=sa.String(200), existing_type=sa.String(60))


def downgrade() -> None:
    op.alter_column("test_runs", "environment", type_=sa.String(60), existing_type=sa.String(200))
    op.alter_column("test_results", "project", type_=sa.String(30), existing_type=sa.String(40))
    op.alter_column("test_results", "scenario_id", type_=sa.String(20), existing_type=sa.String(60))
    op.alter_column("test_results", "case_id", type_=sa.String(40), existing_type=sa.String(80))
