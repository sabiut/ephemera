"""add builds and repository build slots

Revision ID: a7d2e8f5b6c3
Revises: f6c1d7e4a5b2
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d2e8f5b6c3'
down_revision = 'f6c1d7e4a5b2'
branch_labels = None
depends_on = None


def upgrade():
    # Managed builds: each build of a preview's commit, and the build slot a
    # repository builds as. Additive: the migration Job runs before the new
    # pods start.
    op.create_table(
        'builds',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('environment_id', sa.Integer(), sa.ForeignKey('environments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('repository_full_name', sa.String(), nullable=False),
        sa.Column('pr_number', sa.Integer(), nullable=False),
        sa.Column('commit_sha', sa.String(), nullable=False),
        sa.Column('slot', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('cloud_build_id', sa.String(), nullable=True),
        sa.Column('images', sa.JSON(), nullable=True),
        sa.Column('services', sa.JSON(), nullable=True),
        sa.Column('failure_category', sa.String(), nullable=True),
        sa.Column('failure_detail', sa.Text(), nullable=True),
        sa.Column('log_object', sa.String(), nullable=True),
        sa.Column('log_tail', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_builds_id', 'builds', ['id'])
    op.create_index('ix_builds_environment_id', 'builds', ['environment_id'])
    op.create_index('ix_builds_repository_full_name', 'builds', ['repository_full_name'])
    op.add_column('repository_settings', sa.Column('build_slot', sa.Integer(), nullable=True))
    op.create_unique_constraint('uq_repository_settings_build_slot', 'repository_settings', ['build_slot'])


def downgrade():
    op.drop_constraint('uq_repository_settings_build_slot', 'repository_settings', type_='unique')
    op.drop_column('repository_settings', 'build_slot')
    op.drop_index('ix_builds_repository_full_name', table_name='builds')
    op.drop_index('ix_builds_environment_id', table_name='builds')
    op.drop_index('ix_builds_id', table_name='builds')
    op.drop_table('builds')
