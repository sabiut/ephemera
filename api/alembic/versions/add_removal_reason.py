"""add removal_reason to environments

Revision ID: 9c5d1e8f0a6b
Revises: 8b4c0d7e5f9a
Create Date: 2026-09-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c5d1e8f0a6b'
down_revision = '8b4c0d7e5f9a'
branch_labels = None
depends_on = None


def upgrade():
    # Why a preview was removed ("closed", "expired"), recorded before its
    # namespace is deleted so a slow deletion finished by the cleanup job
    # keeps it. Additive: the migration Job runs before the new pods start.
    op.add_column('environments', sa.Column('removal_reason', sa.String(), nullable=True))


def downgrade():
    op.drop_column('environments', 'removal_reason')
