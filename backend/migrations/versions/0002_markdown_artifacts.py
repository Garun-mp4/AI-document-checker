"""Store MarkItDown artifacts and source mapping metadata.

Revision ID: 0002_markdown_artifacts
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0002_markdown_artifacts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("markdown_status", sa.String(length=16), nullable=False, server_default="legacy"))
    op.add_column("documents", sa.Column("analysis_source", sa.String(length=24), nullable=False, server_default="native_fallback"))
    op.add_column("documents", sa.Column("markdown_path", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("markdown_map_path", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("markdown_error", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("markdown_converter_version", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("markdown_char_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("markdown_line_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("markdown_mapping_json", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_index("ix_documents_markdown_status", "documents", ["markdown_status"])

    op.add_column("chunks", sa.Column("content_source", sa.String(length=24), nullable=False, server_default="native"))
    op.add_column("chunks", sa.Column("markdown_line_start", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("markdown_line_end", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("markdown_char_start", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("markdown_char_end", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("mapping_confidence", sa.String(length=16), nullable=True))

    op.alter_column("documents", "markdown_status", server_default=None)
    op.alter_column("documents", "analysis_source", server_default=None)
    op.alter_column("documents", "markdown_char_count", server_default=None)
    op.alter_column("documents", "markdown_line_count", server_default=None)
    op.alter_column("documents", "markdown_mapping_json", server_default=None)
    op.alter_column("chunks", "content_source", server_default=None)


def downgrade() -> None:
    op.drop_column("chunks", "mapping_confidence")
    op.drop_column("chunks", "markdown_char_end")
    op.drop_column("chunks", "markdown_char_start")
    op.drop_column("chunks", "markdown_line_end")
    op.drop_column("chunks", "markdown_line_start")
    op.drop_column("chunks", "content_source")
    op.drop_index("ix_documents_markdown_status", table_name="documents")
    op.drop_column("documents", "markdown_mapping_json")
    op.drop_column("documents", "markdown_line_count")
    op.drop_column("documents", "markdown_char_count")
    op.drop_column("documents", "markdown_converter_version")
    op.drop_column("documents", "markdown_error")
    op.drop_column("documents", "markdown_map_path")
    op.drop_column("documents", "markdown_path")
    op.drop_column("documents", "analysis_source")
    op.drop_column("documents", "markdown_status")
