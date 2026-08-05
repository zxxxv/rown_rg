"""add 'cancelled' to projects status CHECK (구제약 재생성)

사용자가 실행 도중 취소할 수 있도록 projects.status에 'cancelled'를 허용한다.
모델은 ProjectStage enum 파생이라 이미 옳고, DB 제약만 현행 값 전체로 재생성한다.
제약명은 네이밍 컨벤션으로 이중 접두(ck_projects_ck_projects_status)라 raw SQL로 다룬다
(0024와 동일 패턴).

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_projects_ck_projects_status"
_NEW = (
    "'created', 'researching', 'indexing', 'writing', "
    "'reviewing', 'completed', 'archived', 'cancelled'"
)
_OLD = "'created', 'researching', 'indexing', 'writing', 'reviewing', 'completed', 'archived'"


def upgrade() -> None:
    op.execute(f'ALTER TABLE projects DROP CONSTRAINT "{_NAME}"')
    op.execute(f'ALTER TABLE projects ADD CONSTRAINT "{_NAME}" CHECK (status IN ({_NEW}))')


def downgrade() -> None:
    op.execute(f'ALTER TABLE projects DROP CONSTRAINT "{_NAME}"')
    op.execute(f'ALTER TABLE projects ADD CONSTRAINT "{_NAME}" CHECK (status IN ({_OLD}))')
