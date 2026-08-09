"""0034 user_prompts.spec — 개인 에이전트의 구조화 설정(목표 분량 등)

개인 에이전트는 프롬프트 텍스트만 있어 volume_target이 없었다. 그 결과 내가 만든
에이전트를 절에 배정하면 분량 목표가 사라져 절이 짧아졌다(2026-08-10 확인).
목표 분량·검색질의 같은 구조화 값을 opaque JSONB로 받는다.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_prompts",
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("user_prompts", "spec")
