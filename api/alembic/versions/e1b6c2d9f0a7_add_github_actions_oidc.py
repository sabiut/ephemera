"""add repository_settings.github_actions_oidc

Revision ID: e1b6c2d9f0a7
Revises: d0a5b1c8e9f6
Create Date: 2026-10-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1b6c2d9f0a7'
down_revision = 'd0a5b1c8e9f6'
branch_labels = None
depends_on = None


def upgrade():
    # Per repository, off by default: may GitHub Actions in it deploy pipeline
    # previews with OIDC instead of a stored token. Additive.
    op.add_column('repository_settings', sa.Column('github_actions_oidc', sa.Boolean(), nullable=False,
                                                   server_default=sa.false()))


def downgrade():
    op.drop_column('repository_settings', 'github_actions_oidc')
