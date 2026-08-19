"""0042 개인 목차 프리셋 공개 플래그 — 에이전트(0041)와 같은 규약을 프리셋에도.

"누가 좋은 구성을 쓰면 그걸 가져오거나 바로 쓰고 싶다"는 요구(2026-08-19)의
프리셋 몫. 켜면 전 계정의 프리셋 선택지에 뜨고, 라이브러리 '동료 공개' 폴더에서
내 것으로 복사해 갈 수 있다.

작성 규칙(user_prompts kind='rule')은 이번 범위에서 제외한다 — 프로젝트가 규칙을
id로 골라 붙이고 소유자 검증(_validate_rules_config)이 걸려 있어, 공개하려면 해석과
검증을 함께 3층으로 열어야 한다. 사용자 결정으로 다음 차례.

Revision ID: 0042
Revises: 0041
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_presets",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # 공개 목록은 "내 것 빼고 공개된 것 전부"를 프리셋 화면마다 훑는다 — 켜진 행만 담는다.
    op.create_index(
        "ix_user_presets_public",
        "user_presets",
        ["is_public"],
        postgresql_where=sa.text("is_public"),
    )


def downgrade() -> None:
    op.drop_index("ix_user_presets_public", table_name="user_presets")
    op.drop_column("user_presets", "is_public")
