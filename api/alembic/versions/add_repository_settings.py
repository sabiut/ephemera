"""add repository_settings

Revision ID: c3f8b4a1d2e9
Revises: b2e7a3f0c1d8
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3f8b4a1d2e9'
down_revision = 'b2e7a3f0c1d8'
branch_labels = None
depends_on = None


def upgrade():
    # Per-repository preferences (for now: protected preview links). A new
    # table, so the migration Job running before the rollout is safe.
    op.create_table(
        'repository_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('repository_full_name', sa.String(), nullable=False),
        sa.Column('protect_previews', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_by_login', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_repository_settings_id', 'repository_settings', ['id'])
    op.create_index('ix_repository_settings_repository_full_name', 'repository_settings',
                    ['repository_full_name'], unique=True)


def downgrade():
    op.drop_index('ix_repository_settings_repository_full_name', table_name='repository_settings')
    op.drop_index('ix_repository_settings_id', table_name='repository_settings')
    op.drop_table('repository_settings')
