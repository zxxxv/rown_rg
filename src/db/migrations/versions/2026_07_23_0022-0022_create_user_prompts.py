"""create user_prompts table (personal prompt layer)

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-23

개인 프롬프트 레이어(사용자별 커스텀 에이전트/작성 규칙). 시스템 카탈로그(src/prompts
파일) 위에 얹는 오버레이 — 해석은 개인→시스템 폴백.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_prompts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("base_ref", sa.String(length=100), nullable=True),
        sa.Column("cat", sa.String(length=100), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("kind IN ('agent', 'rule')", name="user_prompts_kind_check"),
        sa.UniqueConstraint(
            "owner_id", "kind", "name", name="uq_user_prompts_owner_kind_name"
        ),
    )
    op.create_index("ix_user_prompts_owner_id", "user_prompts", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_user_prompts_owner_id", table_name="user_prompts")
    op.drop_table("user_prompts")
