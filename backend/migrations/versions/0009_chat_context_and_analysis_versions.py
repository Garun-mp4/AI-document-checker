"""Persist chat context boundaries, generation states and analysis choices."""

import sqlalchemy as sa
from alembic import op

revision = "0009_chat_context_analysis"
down_revision = "0008_chat_library_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("context_epoch", sa.Integer(), server_default="0", nullable=False))
    op.add_column("messages", sa.Column("context_epoch", sa.Integer(), server_default="0", nullable=False))
    op.add_column("messages", sa.Column("reply_to_message_id", sa.Uuid(), nullable=True))
    op.add_column("messages", sa.Column("generation_status", sa.String(length=16), server_default="complete", nullable=False))
    op.add_column("messages", sa.Column("generation_error", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("source_version", sa.Integer(), nullable=True))
    op.add_column("document_versions", sa.Column("analysis_model", sa.String(length=120), nullable=True))
    op.add_column("document_versions", sa.Column("analysis_reasoning_effort", sa.String(length=20), nullable=True))
    op.create_foreign_key("fk_messages_reply_to_message_id_messages", "messages", "messages", ["reply_to_message_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_messages_reply_to_message_id", "messages", ["reply_to_message_id"])
    op.create_index("ix_messages_chat_epoch", "messages", ["chat_id", "context_epoch", "created_at"])
    op.create_check_constraint("ck_messages_generation_status", "messages", "generation_status IN ('complete','streaming','interrupted')")
    # Attach legacy assistant rows to their nearest preceding user question.
    op.execute("""
        UPDATE messages AS assistant
        SET reply_to_message_id = (
            SELECT question.id FROM messages AS question
            WHERE question.chat_id = assistant.chat_id AND question.role = 'user'
              AND (question.created_at, question.id) < (assistant.created_at, assistant.id)
            ORDER BY question.created_at DESC, question.id DESC LIMIT 1
        )
        WHERE assistant.role = 'assistant' AND assistant.reply_to_message_id IS NULL
    """)
    op.execute("""
        UPDATE document_versions AS version
        SET analysis_model = job.parameters->>'model',
            analysis_reasoning_effort = job.parameters->>'reasoning_effort'
        FROM processing_jobs AS job
        WHERE job.document_id = version.document_id AND job.version = version.number
          AND job.state = 'succeeded'
          AND version.analysis_model IS NULL
    """)


def downgrade() -> None:
    op.drop_constraint("ck_messages_generation_status", "messages", type_="check")
    op.drop_index("ix_messages_chat_epoch", table_name="messages")
    op.drop_index("ix_messages_reply_to_message_id", table_name="messages")
    op.drop_constraint("fk_messages_reply_to_message_id_messages", "messages", type_="foreignkey")
    op.drop_column("document_versions", "analysis_reasoning_effort")
    op.drop_column("document_versions", "analysis_model")
    op.drop_column("messages", "source_version")
    op.drop_column("messages", "generation_error")
    op.drop_column("messages", "generation_status")
    op.drop_column("messages", "reply_to_message_id")
    op.drop_column("messages", "context_epoch")
    op.drop_column("chats", "context_epoch")
