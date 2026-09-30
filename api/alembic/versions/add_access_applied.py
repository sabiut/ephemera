"""add access_applied to environments

Revision ID: d4a9c5b2e3f0
Revises: c3f8b4a1d2e9
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a9c5b2e3f0'
down_revision = 'c3f8b4a1d2e9'
branch_labels = None
depends_on = None


def upgrade():
    # Which access the preview's routes actually have in the cluster
    # ("public" or "protected"), so the dashboard can say when a change is
    # in effect. Additive: the migration Job runs before the new pods start.
    op.add_column('environments', sa.Column('access_applied', sa.String(), nullable=True))


def downgrade():
    op.drop_column('environments', 'access_applied')
