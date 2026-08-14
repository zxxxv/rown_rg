"""설계 브리프 게이트 — projects.status 'planning' + review_points.gate 'design_brief'

수집 전에 사람이 절별 설계(검색 질의·분량·에이전트)를 확인·수정하는 게이트가 생겼다.
모델 쪽 CHECK는 enum 파생이라 이미 옳고, DB 제약만 현행 값 전체로 재생성한다.
제약명은 네이밍 컨벤션 때문에 이중 접두라 raw SQL로 다룬다(0024·0025와 동일 패턴).

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_NAME = "ck_projects_ck_projects_status"
_STATUS_NEW = (
    "'created', 'planning', 'researching', 'indexing', 'writing', "
    "'reviewing', 'completed', 'archived', 'cancelled'"
)
_STATUS_OLD = (
    "'created', 'researching', 'indexing', 'writing', "
    "'reviewing', 'completed', 'archived', 'cancelled'"
)

_GATE_NAME = "ck_review_points_ck_review_points_review_points_gate_check"
_GATE_NEW = (
    "'design_brief', 'source_pool', 'contradiction', 'level_1', 'level_2', 'qa_select', 'final'"
)
_GATE_OLD = "'source_pool', 'contradiction', 'level_1', 'level_2', 'qa_select', 'final'"


def upgrade() -> None:
    op.execute(f'ALTER TABLE projects DROP CONSTRAINT "{_STATUS_NAME}"')
    op.execute(
        f'ALTER TABLE projects ADD CONSTRAINT "{_STATUS_NAME}" CHECK (status IN ({_STATUS_NEW}))'
    )
    op.execute(f'ALTER TABLE review_points DROP CONSTRAINT "{_GATE_NAME}"')
    op.execute(
        f'ALTER TABLE review_points ADD CONSTRAINT "{_GATE_NAME}" CHECK (gate IN ({_GATE_NEW}))'
    )


def downgrade() -> None:
    # 되돌리기 전에 새 값이 남아 있으면 제약 추가가 실패한다 — 실행 중이던 런은
    # 수집 직전으로 되돌린다(planning은 아직 아무것도 안 만든 단계라 손실이 없다).
    op.execute("DELETE FROM review_points WHERE gate = 'design_brief'")
    op.execute("UPDATE projects SET status = 'created' WHERE status = 'planning'")
    op.execute(f'ALTER TABLE projects DROP CONSTRAINT "{_STATUS_NAME}"')
    op.execute(
        f'ALTER TABLE projects ADD CONSTRAINT "{_STATUS_NAME}" CHECK (status IN ({_STATUS_OLD}))'
    )
    op.execute(f'ALTER TABLE review_points DROP CONSTRAINT "{_GATE_NAME}"')
    op.execute(
        f'ALTER TABLE review_points ADD CONSTRAINT "{_GATE_NAME}" CHECK (gate IN ({_GATE_OLD}))'
    )
