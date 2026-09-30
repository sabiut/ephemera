"""add readiness to environments

Revision ID: e5b0d6c3f4a1
Revises: d4a9c5b2e3f0
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b0d6c3f4a1'
down_revision = 'd4a9c5b2e3f0'
branch_labels = None
depends_on = None


def upgrade():
    # {service: {path, status, verified}} from the last readiness check, so
    # the dashboard can tell "working" from "deployed and responding".
    # Additive: the migration Job runs before the new pods start.
    op.add_column('environments', sa.Column('readiness', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('environments', 'readiness')
