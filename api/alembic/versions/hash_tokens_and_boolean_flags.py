"""hash api tokens at rest, use real booleans, unique PR per repo

Revision ID: 4d0e6f3a1b5c
Revises: 3c9d5e2f0a4b
Create Date: 2026-09-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4d0e6f3a1b5c'
down_revision = '3c9d5e2f0a4b'
branch_labels = None
depends_on = None


def upgrade():
    # --- api_tokens: store a SHA-256 hash instead of the raw token ---
    op.alter_column('api_tokens', 'token', new_column_name='token_hash')
    op.execute(
        "UPDATE api_tokens SET token_hash = encode(sha256(convert_to(token_hash, 'UTF8')), 'hex') "
        "WHERE token_hash LIKE 'eph_%'"
    )
    op.drop_index('ix_api_tokens_token', table_name='api_tokens')
    op.create_index('ix_api_tokens_token_hash', 'api_tokens', ['token_hash'], unique=True)

    # --- is_active: integer -> boolean ---
    for table in ('api_tokens', 'cloud_credentials'):
        op.execute(f"UPDATE {table} SET is_active = 1 WHERE is_active IS NULL")
        op.alter_column(
            table, 'is_active',
            type_=sa.Boolean(),
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text('true'),
            postgresql_using='is_active <> 0',
        )

    # --- one environment per (repository, PR) ---
    op.create_unique_constraint('uq_environments_repo_pr', 'environments', ['repository_full_name', 'pr_number'])


def downgrade():
    op.drop_constraint('uq_environments_repo_pr', 'environments', type_='unique')

    for table in ('api_tokens', 'cloud_credentials'):
        op.alter_column(
            table, 'is_active',
            type_=sa.Integer(),
            existing_type=sa.Boolean(),
            nullable=True,
            server_default='1',
            postgresql_using='CASE WHEN is_active THEN 1 ELSE 0 END',
        )

    # Hashes cannot be reversed; existing tokens become unusable after downgrade.
    op.drop_index('ix_api_tokens_token_hash', table_name='api_tokens')
    op.alter_column('api_tokens', 'token_hash', new_column_name='token')
    op.create_index('ix_api_tokens_token', 'api_tokens', ['token'], unique=True)
