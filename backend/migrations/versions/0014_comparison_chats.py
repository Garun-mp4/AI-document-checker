"""Add explicit, version-pinned multi-document comparison chats."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_comparison_chats"
down_revision = "0013_ui_target_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_chats_scope", "chats", type_="check")
    op.drop_constraint("ck_chats_scope_document", "chats", type_="check")
    op.create_check_constraint(
        "ck_chats_scope",
        "chats",
        "scope IN ('document','application','comparison')",
    )
    op.create_check_constraint(
        "ck_chats_scope_document",
        "chats",
        "(scope = 'document' AND document_id IS NOT NULL) OR "
        "(scope IN ('application','comparison') AND document_id IS NULL)",
    )
    op.create_table(
        "chat_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("is_selected", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_chat_documents_position"),
        sa.CheckConstraint("source_version >= 1", name="ck_chat_documents_source_version"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "document_id", name="uq_chat_documents_chat_document"),
    )
    op.create_index("ix_chat_documents_chat_id", "chat_documents", ["chat_id"])
    op.create_index("ix_chat_documents_document_id", "chat_documents", ["document_id"])
    op.create_index("ix_chat_documents_selected", "chat_documents", ["chat_id", "is_selected", "position"])
    op.add_column(
        "messages",
        sa.Column(
            "citation_snapshots",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    comparison_chats = bind.scalar(sa.text("SELECT count(*) FROM chats WHERE scope = 'comparison'"))
    citations_with_snapshots = bind.scalar(
        sa.text("SELECT count(*) FROM messages WHERE citation_snapshots <> '[]'::jsonb")
    )
    if comparison_chats or citations_with_snapshots:
        raise RuntimeError(
            "Cannot downgrade while comparison chats or citation snapshots exist; refusing to discard saved work."
        )

    op.drop_column("messages", "citation_snapshots")
    op.drop_index("ix_chat_documents_selected", table_name="chat_documents")
    op.drop_index("ix_chat_documents_document_id", table_name="chat_documents")
    op.drop_index("ix_chat_documents_chat_id", table_name="chat_documents")
    op.drop_table("chat_documents")
    op.drop_constraint("ck_chats_scope_document", "chats", type_="check")
    op.drop_constraint("ck_chats_scope", "chats", type_="check")
    op.create_check_constraint("ck_chats_scope", "chats", "scope IN ('document','application')")
    op.create_check_constraint(
        "ck_chats_scope_document",
        "chats",
        "(scope = 'document' AND document_id IS NOT NULL) OR "
        "(scope = 'application' AND document_id IS NULL)",
    )
