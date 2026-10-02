"""Record the model settings used for saved assistant messages."""

import sqlalchemy as sa
from alembic import op

revision = '0007_message_model_metadata'
down_revision = '0006_progress_timestamps'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('model', sa.String(length=120), nullable=True))
    op.add_column('messages', sa.Column('reasoning_effort', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'reasoning_effort')
    op.drop_column('messages', 'model')
