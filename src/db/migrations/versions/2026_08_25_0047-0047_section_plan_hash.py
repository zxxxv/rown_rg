"""0047 절 계획 지문 — "미반영" 판정의 열쇠.

보고서는 완성 순간이 끝이 아니다(2026-08-25 설계 전환). 품질을 보고 목차를 고치거나
자료를 빼면 그 변경이 본문에 닿기 전까지 설계와 본문이 어긋난 채로 남는데, 지금까지는
그 어긋남을 알 길이 없어 "완료면 설정 동결"로 막아 두었다.

이 칸은 절 본문을 쓸 때의 계획 지문(services/sections/drift.content_fingerprint)이다.
현재 목차 정본의 지문과 다르면 그 절은 "목차 수정 미반영"이다.

기존 행은 빈 문자열로 남는다 — 판정에서 제외된다(안 그러면 기존 보고서가 통째로
미반영으로 뜬다). 다음 재작성 때 지문이 채워지며 자연히 편입된다.

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sections",
        sa.Column("plan_hash", sa.String(length=40), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sections", "plan_hash")
