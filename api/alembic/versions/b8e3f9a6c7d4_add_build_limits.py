"""add build approvals and image cleanup marker

Revision ID: b8e3f9a6c7d4
Revises: a7d2e8f5b6c3
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8e3f9a6c7d4'
down_revision = 'a7d2e8f5b6c3'
branch_labels = None
depends_on = None


def upgrade():
    # Managed builds limits: approvals to build a fork's commit, and when a
    # build's image tags were deleted. Additive: the migration Job runs
    # before the new pods start.
    op.create_table(
        'build_approvals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('repository_full_name', sa.String(), nullable=False),
        sa.Column('pr_number', sa.Integer(), nullable=False),
        sa.Column('commit_sha', sa.String(), nullable=False),
        sa.Column('approved_by_login', sa.String(), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('repository_full_name', 'pr_number', 'commit_sha', name='uq_build_approvals_commit'),
    )
    op.create_index('ix_build_approvals_id', 'build_approvals', ['id'])
    op.add_column('builds', sa.Column('images_deleted_at', sa.DateTime(timezone=True), nullable=True))
    # Builds outlive their preview's record (deleted a week after teardown),
    # so the month's used build minutes stay counted.
    op.alter_column('builds', 'environment_id', existing_type=sa.Integer(), nullable=True)
    op.drop_constraint('builds_environment_id_fkey', 'builds', type_='foreignkey')
    op.create_foreign_key('builds_environment_id_fkey', 'builds', 'environments', ['environment_id'], ['id'],
                          ondelete='SET NULL')


def downgrade():
    op.drop_constraint('builds_environment_id_fkey', 'builds', type_='foreignkey')
    op.execute("DELETE FROM builds WHERE environment_id IS NULL")
    op.create_foreign_key('builds_environment_id_fkey', 'builds', 'environments', ['environment_id'], ['id'],
                          ondelete='CASCADE')
    op.alter_column('builds', 'environment_id', existing_type=sa.Integer(), nullable=False)
    op.drop_column('builds', 'images_deleted_at')
    op.drop_index('ix_build_approvals_id', table_name='build_approvals')
    op.drop_table('build_approvals')
