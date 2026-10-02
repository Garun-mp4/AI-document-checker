"""Allow durable application-help chats without documents."""

import sqlalchemy as sa
from alembic import op

revision = "0012_application_chats"
down_revision = "0011_bookmarks_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("scope", sa.String(length=16), server_default="document", nullable=False),
    )
    op.drop_constraint("chats_document_id_key", "chats", type_="unique")
    op.alter_column("chats", "document_id", existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint("ck_chats_scope", "chats", "scope IN ('document','application')")
    op.create_check_constraint(
        "ck_chats_scope_document",
        "chats",
        "(scope = 'document' AND document_id IS NOT NULL) OR "
        "(scope = 'application' AND document_id IS NULL)",
    )
    op.create_index(
        "uq_chats_one_document_chat",
        "chats",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'document' AND document_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    application_chats = bind.scalar(sa.text("SELECT count(*) FROM chats WHERE scope = 'application'"))
    if application_chats:
        raise RuntimeError("Cannot downgrade while application-help chats exist; refusing to delete saved conversations.")

    op.drop_index("uq_chats_one_document_chat", table_name="chats")
    op.drop_constraint("ck_chats_scope_document", "chats", type_="check")
    op.drop_constraint("ck_chats_scope", "chats", type_="check")
    op.alter_column("chats", "document_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("chats", "scope")
    op.create_unique_constraint("chats_document_id_key", "chats", ["document_id"])
