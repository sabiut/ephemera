"""add kept_at to environments

Revision ID: a1d6f2e9b0c7
Revises: 9c5d1e8f0a6b
Create Date: 2026-09-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1d6f2e9b0c7'
down_revision = '9c5d1e8f0a6b'
branch_labels = None
depends_on = None


def upgrade():
    # When someone last chose "Keep available"; restarts the idle timer.
    # Additive: the migration Job runs before the new pods start.
    op.add_column('environments', sa.Column('kept_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('environments', 'kept_at')
