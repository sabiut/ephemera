"""add deployment progress to environments

Revision ID: 8b4c0d7e5f9a
Revises: 7a3b9c6d4e8f
Create Date: 2026-09-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b4c0d7e5f9a'
down_revision = '7a3b9c6d4e8f'
branch_labels = None
depends_on = None


def upgrade():
    # Additive only: the migration Job runs before the new pods start, while
    # the old ones keep serving against this schema.
    op.add_column('environments', sa.Column('stage', sa.String(), nullable=True))
    op.add_column('environments', sa.Column('stage_detail', sa.String(), nullable=True))
    op.add_column('environments', sa.Column('stage_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('environments', sa.Column('deploy_started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('environments', 'deploy_started_at')
    op.drop_column('environments', 'stage_started_at')
    op.drop_column('environments', 'stage_detail')
    op.drop_column('environments', 'stage')
