"""Add privacy-safe local maintenance journal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_local_data_maintenance"
down_revision = "0009_chat_context_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maintenance_events_created_at", "maintenance_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_events_created_at", table_name="maintenance_events")
    op.drop_table("maintenance_events")
