"""add pipeline previews and scoped tokens

Revision ID: d0a5b1c8e9f6
Revises: c9f4a0b7d8e5
Create Date: 2026-10-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd0a5b1c8e9f6'
down_revision = 'c9f4a0b7d8e5'
branch_labels = None
depends_on = None


def upgrade():
    # Previews created by CI pipelines (docs/ci-pipeline.md), named per
    # repository, and API tokens limited to them. Additive: the migration
    # Job runs before the new pods start, and existing rows are PR previews.
    op.add_column('environments', sa.Column('source', sa.String(), nullable=False, server_default='pull_request'))
    op.add_column('environments', sa.Column('preview_name', sa.String(), nullable=True))
    op.add_column('environments', sa.Column('linked_pr_number', sa.Integer(), nullable=True))
    op.add_column('environments', sa.Column('image_overrides', sa.JSON(), nullable=True))
    op.add_column('environments', sa.Column('compose_override', sa.Text(), nullable=True))
    op.add_column('environments', sa.Column('serial', sa.Integer(), nullable=True))
    op.add_column('environments', sa.Column('pipeline_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('environments', 'pr_number', existing_type=sa.Integer(), nullable=True)
    op.create_unique_constraint('uq_environments_repo_preview_name', 'environments',
                                ['repository_full_name', 'preview_name'])
    op.add_column('api_tokens', sa.Column('scope', sa.String(), nullable=True))
    op.add_column('api_tokens', sa.Column('repository_full_name', sa.String(), nullable=True))


def downgrade():
    op.drop_column('api_tokens', 'repository_full_name')
    op.drop_column('api_tokens', 'scope')
    op.drop_constraint('uq_environments_repo_preview_name', 'environments', type_='unique')
    op.execute("DELETE FROM deployments WHERE environment_id IN (SELECT id FROM environments WHERE pr_number IS NULL)")
    op.execute("DELETE FROM environments WHERE pr_number IS NULL")
    op.alter_column('environments', 'pr_number', existing_type=sa.Integer(), nullable=False)
    for column in ('pipeline_expires_at', 'serial', 'compose_override', 'image_overrides', 'linked_pr_number',
                   'preview_name', 'source'):
        op.drop_column('environments', column)
