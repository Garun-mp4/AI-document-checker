"""Add version-bound bookmarks and supplementary analysis results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_bookmarks_analysis"
down_revision = "0010_local_data_maintenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_bookmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "source_id", name="uq_document_bookmarks_source"),
    )
    op.create_index("ix_document_bookmarks_document_id", "document_bookmarks", ["document_id"])

    op.create_table(
        "additional_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("mode IN ('brief','detailed','tasks','risks')", name="ck_additional_analyses_mode"),
        sa.ForeignKeyConstraint(
            ["document_id", "analysis_version"],
            ["document_versions.document_id", "document_versions.number"],
            ondelete="CASCADE",
            name="fk_additional_analyses_document_version",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_additional_analyses_document_version_created",
        "additional_analyses",
        ["document_id", "analysis_version", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_additional_analyses_document_version_created", table_name="additional_analyses")
    op.drop_table("additional_analyses")
    op.drop_index("ix_document_bookmarks_document_id", table_name="document_bookmarks")
    op.drop_table("document_bookmarks")
