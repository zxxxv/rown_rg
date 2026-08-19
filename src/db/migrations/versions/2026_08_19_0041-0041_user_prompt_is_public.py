"""0041 개인 에이전트 공개 플래그 — 잘 만든 에이전트를 사내가 함께 쓴다.

지금까지 개인 에이전트는 owner_id 스코프라(resolve_analysts) 남이 만든 좋은
에이전트를 쓰려면 같은 행을 계정마다 심어야 했다(8/18 검증런 준비 때 4계정에
수동 복제). is_public을 켜면 그 에이전트가 전 계정의 선택 목록에 뜬다 —
본인 토글, 관리자 승인 없음(2026-08-19 사용자 결정).

kind='rule'에는 의미가 없다(작성 규칙은 프로젝트에서 id로 고르는 소유자 스코프
계약). 컬럼은 공용으로 두되 켜는 것은 API가 agent로 막는다.

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_prompts",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # 공개 목록은 "내 것 빼고 공개된 agent 전부"를 매번 훑는다(에이전트 선택 화면·
    # 런 시작 스냅샷). 켜진 행만 담는 부분 인덱스면 색인이 공개분 크기로 유지된다.
    op.create_index(
        "ix_user_prompts_public_agents",
        "user_prompts",
        ["kind", "is_public"],
        postgresql_where=sa.text("is_public"),
    )


def downgrade() -> None:
    op.drop_index("ix_user_prompts_public_agents", table_name="user_prompts")
    op.drop_column("user_prompts", "is_public")
