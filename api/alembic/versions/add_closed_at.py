"""add closed_at to environments

Revision ID: 7a3b9c6d4e8f
Revises: 6f2a8b5c3d7e
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7a3b9c6d4e8f'
down_revision = '6f2a8b5c3d7e'
branch_labels = None
depends_on = None


def upgrade():
    # When the PR closed; cleared on reopen. Deploy and teardown tasks check
    # it under the environment lock so a queued task cannot act against the
    # PR's current state.
    op.add_column('environments', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('environments', 'closed_at')
