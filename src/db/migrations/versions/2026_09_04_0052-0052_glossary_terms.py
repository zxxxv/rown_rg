"""0052 정본 용어집 — 사람이 확정한 표기가 채굴 용어표보다 우선하는 저장소.

채굴은 자료가 말한 대로 적립할 뿐이라 자료끼리 표기가 갈리면 상충으로 남고,
그때 파이프라인은 표기 강제를 빼는 보수 모드로 눕는다(2026-08-28 3층 방어).
검토 화면에서 사람이 경고를 처리하며 확정한 표기를 여기 승격하면 주입이 강제하고
검사가 잣대로 삼아 상충이 근원에서 소멸한다.

2층 범위(2026-09-04 사용자 결정): project_id NULL = 회사 공유, 값 = 프로젝트
덮어쓰기(같은 term_key면 프로젝트 행 우선). 유일성은 층별 부분 유니크 색인 2개 —
NULL이 유니크 제약에서 서로 다른 값 취급이라 한 색인으로는 회사 공유 중복을 못 막는다.

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "glossary_terms",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("term_key", sa.String(length=160), nullable=False),
        sa.Column("en", sa.String(length=160), nullable=True),
        sa.Column("abbr", sa.String(length=40), nullable=True),
        sa.Column("ko", sa.String(length=120), nullable=False),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), server_default="manual", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_glossary_terms_project", "glossary_terms", ["project_id"])
    op.create_index(
        "uq_glossary_terms_org_key",
        "glossary_terms",
        ["term_key"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "uq_glossary_terms_project_key",
        "glossary_terms",
        ["project_id", "term_key"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_glossary_terms_project_key", table_name="glossary_terms")
    op.drop_index("uq_glossary_terms_org_key", table_name="glossary_terms")
    op.drop_index("ix_glossary_terms_project", table_name="glossary_terms")
    op.drop_table("glossary_terms")
