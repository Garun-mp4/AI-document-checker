"""Persist OCR processing state and quality metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0004_ocr_metadata"
down_revision = "0003_markdown_checksum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("ocr_status", sa.String(length=16), nullable=False, server_default="not_needed"))
    op.add_column("documents", sa.Column("ocr_language", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("ocr_page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("ocr_confidence", sa.Float(), nullable=True))
    op.add_column("documents", sa.Column("ocr_error", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("ocr_engine_version", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("ocr_char_count", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_documents_ocr_status", "documents", ["ocr_status"])
    op.alter_column("documents", "ocr_status", server_default=None)
    op.alter_column("documents", "ocr_char_count", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_documents_ocr_status", table_name="documents")
    op.drop_column("documents", "ocr_char_count")
    op.drop_column("documents", "ocr_engine_version")
    op.drop_column("documents", "ocr_error")
    op.drop_column("documents", "ocr_confidence")
    op.drop_column("documents", "ocr_page_count")
    op.drop_column("documents", "ocr_language")
    op.drop_column("documents", "ocr_status")
