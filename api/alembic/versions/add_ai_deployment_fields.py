"""add ai deployment fields to deployments table

Revision ID: 3c9d5e2f0a4b
Revises: 2b8c4d1e9f3a
Create Date: 2025-12-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3c9d5e2f0a4b'
down_revision = '2b8c4d1e9f3a'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('deployments', sa.Column('ai_generated', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('deployments', sa.Column('ai_plan', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('deployments', 'ai_plan')
    op.drop_column('deployments', 'ai_generated')
