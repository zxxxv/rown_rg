"""0045 완성 선언 분리 + 버전 사유 확장.

진화형 작성(2026-08-24 설계): 파이프라인 완주(completed)는 '사이클 완료'일 뿐이고,
납품 확정은 사람이 누른다. projects.finalized_at이 그 선언의 기록이다 — NULL이면
"검토 중", 값이 있으면 "최종 확정". 재개(reopen)로 completed를 떠나면 리스너가
completed_at과 함께 해제한다.

report_versions.reason은 String(10)이라 절 좌표가 붙는 사유("rewrite:2.2")를 못
담았다 — 재작성·수동 저장 트리거 확장(같은 설계)과 함께 30자로 넓힌다.

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "report_versions",
        "reason",
        type_=sa.String(length=30),
        existing_type=sa.String(length=10),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "report_versions",
        "reason",
        type_=sa.String(length=10),
        existing_type=sa.String(length=30),
        existing_nullable=False,
    )
    op.drop_column("projects", "finalized_at")
