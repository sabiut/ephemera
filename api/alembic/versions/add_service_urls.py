"""add service_urls to environments

Revision ID: 6f2a8b5c3d7e
Revises: 5e1f7a4b2c6d
Create Date: 2026-09-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6f2a8b5c3d7e'
down_revision = '5e1f7a4b2c6d'
branch_labels = None
depends_on = None


def upgrade():
    # The real public URL of every exposed service. environment_url keeps the
    # single link a reviewer opens; before this it held a namespace-level
    # hostname that no ingress ever served.
    op.add_column('environments', sa.Column('service_urls', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('environments', 'service_urls')
