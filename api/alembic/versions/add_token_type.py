"""add token_type to api_tokens

Revision ID: 5e1f7a4b2c6d
Revises: 4d0e6f3a1b5c
Create Date: 2026-09-20 00:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5e1f7a4b2c6d'
down_revision = '4d0e6f3a1b5c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'api_tokens',
        sa.Column('token_type', sa.String(), nullable=False, server_default='api'),
    )
    # Tokens minted by the dashboard login were always named this way
    op.execute("UPDATE api_tokens SET token_type = 'session' WHERE name = 'Web Dashboard Session'")


def downgrade():
    op.drop_column('api_tokens', 'token_type')
