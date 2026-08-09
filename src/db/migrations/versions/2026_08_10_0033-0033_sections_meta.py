"""0033 sections.meta — 절 단위 생성 지표(근거 수·분량 목표 하향 여부)

재료가 부족한 절은 분량 목표를 내려서 쓴다(수치 창작 방지). 그 사실을 본문에
"자료 한계" 문장으로 적으면 납품물이 더러워지므로, 절 메타로 빼내 화면에서만
배지로 알린다. opaque JSONB라 지표가 늘어도 마이그레이션이 더 필요 없다.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sections",
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sections", "meta")
