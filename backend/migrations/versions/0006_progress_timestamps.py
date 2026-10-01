"""Persist queue wait and processing-stage timing for M04."""

from alembic import op
import sqlalchemy as sa


revision = '0006_progress_timestamps'
down_revision = '0005_processing_queue'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('processing_jobs', sa.Column('queued_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('processing_jobs', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('processing_jobs', sa.Column('stage_started_at', sa.DateTime(timezone=True), nullable=True))
    op.execute('UPDATE processing_jobs SET queued_at = created_at WHERE queued_at IS NULL')
    op.alter_column('processing_jobs', 'queued_at', nullable=False)


def downgrade() -> None:
    op.drop_column('processing_jobs', 'stage_started_at')
    op.drop_column('processing_jobs', 'started_at')
    op.drop_column('processing_jobs', 'queued_at')
