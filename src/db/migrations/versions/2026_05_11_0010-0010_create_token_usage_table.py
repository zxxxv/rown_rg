"""create token_usage table

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_usage",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("model", sa.VARCHAR(50), nullable=False),
        sa.Column("operation", sa.VARCHAR(50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "cached_input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column(
            "mode",
            sa.VARCHAR(10),
            sa.CheckConstraint(
                "mode IN ('live', 'record', 'replay')",
                name="ck_token_usage_mode",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_token_usage_user_created", "token_usage", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_token_usage_project_created", "token_usage", ["project_id", "created_at"]
    )
    op.create_index("ix_token_usage_model", "token_usage", ["model"])
    op.create_index("ix_token_usage_operation", "token_usage", ["operation"])


def downgrade() -> None:
    op.drop_index("ix_token_usage_operation", table_name="token_usage")
    op.drop_index("ix_token_usage_model", table_name="token_usage")
    op.drop_index("ix_token_usage_project_created", table_name="token_usage")
    op.drop_index("ix_token_usage_user_created", table_name="token_usage")
    op.drop_table("token_usage")
