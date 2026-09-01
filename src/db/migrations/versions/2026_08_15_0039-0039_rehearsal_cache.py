"""0039 검색 리허설 — projects.index_version + section_rehearsals 캐시.

색인 직후 절마다 작성과 같은 검색을 미리 돌려(리허설) 근거 충분성을 3밴드로
판정하고, 그 결과(청크 목록)를 작성이 그대로 재사용한다. 리허설과 작성이 다른
근거를 보면 판정이 무의미해지므로 캐시 키에 index_version을 넣는다 — 청크가
변하는 사건(색인·0청크 자동 제외·채택 토글)마다 증가해 낡은 캐시를 무효화한다.

section_rehearsals는 프로젝트당 절당 1행(최신만) — 크래시 재개 시 리허설을
다시 돌지 않고 이어 쓰기 위한 영속이기도 하다.

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("index_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "section_rehearsals",
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("section_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("index_version", sa.Integer(), nullable=False),
        # ok(충분) | hyde(HyDE 재검색 수행) | empty(공백) | live(리허설 없이 작성 시점 기록)
        sa.Column("band", sa.String(10), nullable=False),
        sa.Column("floor_passed", sa.Integer(), nullable=False),
        sa.Column("needed", sa.Integer(), nullable=False),
        sa.Column("hyde_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # [{"id": "<chunk uuid>", "score": 0.87}, ...] — 순위 순. 본문은 chunks가 진실.
        sa.Column("chunks", JSONB, nullable=False, server_default="[]"),
        # {"constructive": bool, "raptor_gap": bool} — 공백 경고의 세부 분류.
        sa.Column("warnings", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("section_rehearsals")
    op.drop_column("projects", "index_version")
