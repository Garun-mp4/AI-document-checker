"""Persist validated UI help target metadata on assistant messages."""

import sqlalchemy as sa
from alembic import op

revision = "0013_ui_target_metadata"
down_revision = "0012_application_chats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("ui_target_id", sa.String(length=80), nullable=True))
    op.add_column("messages", sa.Column("ui_target_catalog_version", sa.String(length=32), nullable=True))
    op.add_column("messages", sa.Column("ui_target_build_id", sa.String(length=200), nullable=True))
    op.create_check_constraint(
        "ck_messages_ui_target_metadata",
        "messages",
        "(ui_target_id IS NULL AND ui_target_catalog_version IS NULL AND ui_target_build_id IS NULL) OR "
        "(ui_target_id IS NOT NULL AND ui_target_catalog_version IS NOT NULL AND ui_target_build_id IS NOT NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    saved_targets = bind.scalar(sa.text("SELECT count(*) FROM messages WHERE ui_target_id IS NOT NULL"))
    if saved_targets:
        raise RuntimeError("Cannot downgrade while saved UI target metadata exists; refusing to discard it.")

    op.drop_constraint("ck_messages_ui_target_metadata", "messages", type_="check")
    op.drop_column("messages", "ui_target_build_id")
    op.drop_column("messages", "ui_target_catalog_version")
    op.drop_column("messages", "ui_target_id")
