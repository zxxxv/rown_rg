"""0037 users.last_seen_at — 현재 접속중 판정용 하트비트.

관리자 대시보드의 '현재 접속중' KPI(최근 5분 내 활동)를 위해 인증 통과 시각을
기록한다. 쓰기 폭주를 막기 위해 get_current_user에서 60초에 한 번만 갱신하므로
값의 해상도는 분 단위다. last_login_at(기간 활성 사용자)과 역할이 다르다.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_seen_at")
