"""add credentials and tokens

Revision ID: 2b8c4d1e9f3a
Revises: 39b94fc00f53
Create Date: 2025-11-07 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2b8c4d1e9f3a'
down_revision = '39b94fc00f53'
branch_labels = None
depends_on = None


def upgrade():
    # Create cloud_credentials table
    op.create_table(
        'cloud_credentials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.Enum('GCP', 'AWS', 'AZURE', name='cloudprovider'), nullable=False),
        sa.Column('credentials_encrypted', sa.Text(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=True, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cloud_credentials_id'), 'cloud_credentials', ['id'], unique=False)
    op.create_index(op.f('ix_cloud_credentials_provider'), 'cloud_credentials', ['provider'], unique=False)

    # Create api_tokens table
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('token_prefix', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=True, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_api_tokens_id'), 'api_tokens', ['id'], unique=False)
    op.create_index(op.f('ix_api_tokens_token'), 'api_tokens', ['token'], unique=True)
    op.create_index(op.f('ix_api_tokens_token_prefix'), 'api_tokens', ['token_prefix'], unique=False)


def downgrade():
    # Drop api_tokens table
    op.drop_index(op.f('ix_api_tokens_token_prefix'), table_name='api_tokens')
    op.drop_index(op.f('ix_api_tokens_token'), table_name='api_tokens')
    op.drop_index(op.f('ix_api_tokens_id'), table_name='api_tokens')
    op.drop_table('api_tokens')

    # Drop cloud_credentials table
    op.drop_index(op.f('ix_cloud_credentials_provider'), table_name='cloud_credentials')
    op.drop_index(op.f('ix_cloud_credentials_id'), table_name='cloud_credentials')
    op.drop_table('cloud_credentials')

    # Drop enum type
    sa.Enum(name='cloudprovider').drop(op.get_bind(), checkfirst=True)
