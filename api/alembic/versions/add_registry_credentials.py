"""add registry_credentials

Revision ID: b2e7a3f0c1d8
Revises: a1d6f2e9b0c7
Create Date: 2026-09-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2e7a3f0c1d8'
down_revision = 'a1d6f2e9b0c7'
branch_labels = None
depends_on = None


def upgrade():
    # Read-only tokens for pulling a repository's private images. A new
    # table, so the migration Job running before the rollout is safe.
    op.create_table(
        'registry_credentials',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('repository_full_name', sa.String(), nullable=False),
        sa.Column('registry', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('secret_encrypted', sa.Text(), nullable=False),
        sa.Column('created_by_login', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('repository_full_name', 'registry', name='uq_registry_credentials_repo_registry'),
    )
    op.create_index('ix_registry_credentials_id', 'registry_credentials', ['id'])
    op.create_index('ix_registry_credentials_repository_full_name', 'registry_credentials', ['repository_full_name'])


def downgrade():
    op.drop_index('ix_registry_credentials_repository_full_name', table_name='registry_credentials')
    op.drop_index('ix_registry_credentials_id', table_name='registry_credentials')
    op.drop_table('registry_credentials')
