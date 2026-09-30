"""add managed builds plan to repository_settings

Revision ID: f6c1d7e4a5b2
Revises: e5b0d6c3f4a1
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6c1d7e4a5b2'
down_revision = 'e5b0d6c3f4a1'
branch_labels = None
depends_on = None


def upgrade():
    # Managed builds, switched on per repository once a collaborator confirms
    # the detected build plan. Additive: the migration Job runs before the
    # new pods start.
    op.add_column('repository_settings', sa.Column('managed_builds_enabled', sa.Boolean(), nullable=False,
                                                   server_default=sa.false()))
    op.add_column('repository_settings', sa.Column('build_plan_confirmed', sa.JSON(), nullable=True))
    op.add_column('repository_settings', sa.Column('build_plan_confirmed_by', sa.String(), nullable=True))
    op.add_column('repository_settings', sa.Column('build_plan_confirmed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('repository_settings', 'build_plan_confirmed_at')
    op.drop_column('repository_settings', 'build_plan_confirmed_by')
    op.drop_column('repository_settings', 'build_plan_confirmed')
    op.drop_column('repository_settings', 'managed_builds_enabled')
