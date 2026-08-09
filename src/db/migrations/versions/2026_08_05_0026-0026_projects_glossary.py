"""projects.glossary JSONB — 조립 시 생성한 약어 사전({약어: {full, desc}}) 영속화

약어 설명은 assemble에서 LLM 1콜로 만들고 여기 저장한다. 다운로드 재렌더는
순수 코드 원칙(LLM 없음)이라, 렌더가 이 컬럼을 읽어 약어 정리표의 설명을 채운다.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("glossary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "glossary")
