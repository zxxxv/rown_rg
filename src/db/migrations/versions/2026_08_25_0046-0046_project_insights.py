"""0046 시사점 요약 저장 칸.

본문의 시사점·제언 절은 그 자체로 3~5쪽이라(프리셋 min/max_chars 4500~7500) 결정권자가
훑기엔 길다. 조립 시 LLM 1콜로 2~3쪽 브리핑을 만들어 여기에 담는다.

**원본 보고서는 안 건드린다** — 이 요약은 HWPX에 실리지 않고 웹 /insights에서만 본다
(2026-08-25 사용자 결정). 그래서 렌더 경로에는 배선이 없다.

모양: {"content": "## 핵심 요약\\n\\n□ …", "source_sections": ["6.2 시사점 및 제언"],
"model": "claude-sonnet-4-6"}. 생성 실패·미생성은 NULL — 화면이 빈 상태를 보여줄 뿐이다.

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("insights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "insights")
