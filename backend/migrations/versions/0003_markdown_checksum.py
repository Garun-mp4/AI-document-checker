"""Store a checksum for generated Markdown artifacts.

Revision ID: 0003_markdown_checksum
Revises: 0002_markdown_artifacts
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_markdown_checksum"
down_revision = "0002_markdown_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("markdown_checksum", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "markdown_checksum")
