"""0048 절 잠금 — 손본 절을 AI가 다시 덮어쓰지 못하게.

묶음 재작성은 한 번에 수십 절을 갈아엎는다(전체 실측 $15.5). 그런데 사람이 공들여
고쳐 놓은 절이 그 안에 섞여 있으면, "전부 다시 쓰기"를 누르는 순간 그 손질이 사라진다.
되돌리기(report_versions)가 있어도 어느 절이 덮였는지 찾아 되돌리는 건 사람 몫이다.

이 칸이 켜진 절은 AI 경로(절 재작성·블록 재작성·묶음 재작성)에서 제외된다. 사람이
직접 고치는 것은 막지 않는다 — 잠근 사람이 그 사람이다.

미반영 판정에서는 빼지 **않는다**: 잠갔다고 설계와 어긋난 사실이 사라지지는 않는다.
화면이 "미반영이지만 잠겨 있다"고 보여 주고, 다시 쓰려면 사람이 먼저 푼다.

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sections",
        sa.Column("locked", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sections", "locked")
