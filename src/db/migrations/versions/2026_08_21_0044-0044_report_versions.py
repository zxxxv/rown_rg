"""0044 보고서 버전 스냅샷 테이블 — sections의 유일한 보존 지점.

sections는 절당 1행 덮어쓰기라(재작성·편집·재조립 전부) 이전 내용이 아무 데도
안 남았다. 시차 작성(재개) + diff 버전 관리(2026-08-21 설계)의 저장부:
조립 완성·재개 직전에 절 전량을 JSONB로 얼린다. append-only, diff는 조회 시 계산.

Revision ID: 0044
Revises: 0043
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=10), nullable=False),
        sa.Column("sections", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("content_hash", sa.String(length=40), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "version_no", name="uq_report_version_no"),
    )
    op.create_index("ix_report_versions_project_id", "report_versions", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_report_versions_project_id", table_name="report_versions")
    op.drop_table("report_versions")
