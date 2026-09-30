"""add events and builds.queued_seconds

Revision ID: c9f4a0b7d8e5
Revises: b8e3f9a6c7d4
Create Date: 2026-10-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9f4a0b7d8e5'
down_revision = 'b8e3f9a6c7d4'
branch_labels = None
depends_on = None


def upgrade():
    # The admin metrics page: events on the way to a working preview, and
    # how long each build waited for a machine. Additive: the migration Job
    # runs before the new pods start.
    op.create_table(
        'events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('repository_full_name', sa.String(), nullable=True),
        sa.Column('environment_id', sa.Integer(), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_events_id', 'events', ['id'])
    op.create_index('ix_events_kind', 'events', ['kind'])
    op.create_index('ix_events_repository_full_name', 'events', ['repository_full_name'])
    op.create_index('ix_events_created_at', 'events', ['created_at'])
    op.add_column('builds', sa.Column('queued_seconds', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('builds', 'queued_seconds')
    op.drop_index('ix_events_created_at', table_name='events')
    op.drop_index('ix_events_repository_full_name', table_name='events')
    op.drop_index('ix_events_kind', table_name='events')
    op.drop_index('ix_events_id', table_name='events')
    op.drop_table('events')
