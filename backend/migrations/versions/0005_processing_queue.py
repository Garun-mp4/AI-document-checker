"""Durable jobs and immutable processing revisions; preserve legacy results."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005_processing_queue'
down_revision = '0004_ocr_metadata'
branch_labels = depends_on = None


def upgrade():
    op.add_column('documents', sa.Column('active_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('documents', sa.Column('next_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('documents', sa.Column('input_checksum', sa.String(64)))
    for table in ('chunks', 'insights'):
        op.add_column(table, sa.Column('version', sa.Integer(), nullable=False, server_default='0'))
    op.create_unique_constraint('uq_chunks_version_ordinal', 'chunks', ['document_id', 'version', 'ordinal'])
    op.drop_constraint('uq_insights_document_key', 'insights', type_='unique')
    op.create_unique_constraint('uq_insights_version_key', 'insights', ['document_id', 'version', 'key'])
    op.create_table('document_versions',
        sa.Column('document_id', sa.Uuid(), sa.ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('number', sa.Integer(), primary_key=True),
        sa.Column('chunk_version', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table('processing_jobs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('document_id', sa.Uuid(), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('operation', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('input_version', sa.String(64)),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('stage', sa.String(32), nullable=False),
        sa.Column('progress', postgresql.JSONB(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('heartbeat', sa.DateTime(timezone=True)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('owner', sa.Uuid()),
        sa.Column('error', sa.Text()),
        sa.Column('error_code', sa.String(40)),
        sa.Column('parameters', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('finished_at', sa.DateTime(timezone=True)))
    for column in ('document_id', 'state', 'lease_until'):
        op.create_index(f'ix_processing_jobs_{column}', 'processing_jobs', [column])
    op.create_index('uq_jobs_active_document', 'processing_jobs', ['document_id'], unique=True,
                    postgresql_where=sa.text("state IN ('queued','running','cancelling')"))
    # Existing results stay at version zero; no automatic reanalysis of ready files.
    op.execute("""INSERT INTO document_versions(document_id,number,chunk_version,state,snapshot)
        SELECT id,0,0,'ready', to_jsonb(d) - 'storage_path' - 'id' - 'created_at' - 'updated_at'
        FROM documents d WHERE chunk_count > 0""")


def downgrade():
    raise RuntimeError('Processing revisions cannot be discarded automatically; restore a verified database backup instead.')
