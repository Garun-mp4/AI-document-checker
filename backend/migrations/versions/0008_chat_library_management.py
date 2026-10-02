"""Add durable chat titles, pinning and optimistic revision checks."""

import sqlalchemy as sa
from alembic import op

revision = "0008_chat_library_management"
down_revision = "0007_message_model_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("title", sa.String(length=72), nullable=True))
    op.add_column("chats", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chats", sa.Column("revision", sa.Integer(), server_default="1", nullable=False))
    op.create_index("ix_chats_pinned_at", "chats", ["pinned_at"])


def downgrade() -> None:
    op.drop_index("ix_chats_pinned_at", table_name="chats")
    op.drop_column("chats", "revision")
    op.drop_column("chats", "pinned_at")
    op.drop_column("chats", "title")
