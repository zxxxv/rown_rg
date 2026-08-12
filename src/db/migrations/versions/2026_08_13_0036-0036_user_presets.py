"""0036 user_presets — 개인 목차 프리셋(보고서 구성 재사용).

같은 보고서 구성으로 여러 정책을 분석하는 용례(2026-08-12 QA)에서, 목차 편집기로
확정한 구성(장·절·방향·에이전트 배정)을 이름 붙여 저장하고 다음 프로젝트 생성 때
보고서 유형에서 불러온다. 시스템 프리셋(파일) 위의 개인 오버레이 — user_prompts와
같은 패턴.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_presets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("outline", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_user_presets_owner_name"),
    )
    op.create_index("ix_user_presets_owner_id", "user_presets", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_user_presets_owner_id", table_name="user_presets")
    op.drop_table("user_presets")
