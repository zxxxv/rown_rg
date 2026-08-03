"""review_points gate CHECK에 qa_select 추가 (구제약 재생성)

ReviewGate enum에 QA_SELECT가 추가됐지만 초기 마이그레이션의 CHECK 제약은
갱신되지 않아, write 완료 후 QA_SELECT 게이트 행 삽입이 CheckViolation으로
실패했다(2026-08-03 풀사이클 E2E에서 발견). 모델은 enum 파생이라 이미 옳고,
DB 제약만 현행 enum 전체 값으로 재생성한다.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-03

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_NAME = "ck_review_points_ck_review_points_review_points_gate_check"
_GATES = "'source_pool', 'contradiction', 'level_1', 'level_2', 'qa_select', 'final'"


def upgrade() -> None:
    op.execute(f'ALTER TABLE review_points DROP CONSTRAINT "{_OLD_NAME}"')
    op.execute(
        f'ALTER TABLE review_points ADD CONSTRAINT "{_OLD_NAME}" CHECK (gate IN ({_GATES}))'
    )


def downgrade() -> None:
    op.execute(f'ALTER TABLE review_points DROP CONSTRAINT "{_OLD_NAME}"')
    op.execute(
        f'ALTER TABLE review_points ADD CONSTRAINT "{_OLD_NAME}" '
        "CHECK (gate IN ('source_pool', 'contradiction', 'level_1', 'level_2', 'final'))"
    )
