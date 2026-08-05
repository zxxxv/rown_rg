"""create token_usage_retry_queue table

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_usage_retry_queue",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.VARCHAR(10),
            sa.CheckConstraint(
                "status IN ('pending', 'succeeded', 'failed')",
                name="ck_token_usage_retry_queue_token_usage_retry_queue_status_check",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_attempted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_token_usage_retry_queue_status_created",
        "token_usage_retry_queue",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_token_usage_retry_queue_status_created", table_name="token_usage_retry_queue"
    )
    op.drop_table("token_usage_retry_queue")
