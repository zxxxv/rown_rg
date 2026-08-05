"""fix users(email, name) pgroonga indexes to use full-text-search opclass (ILIKE was not index-accelerated)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX ix_users_email_pgroonga")
    op.execute("DROP INDEX ix_users_name_pgroonga")
    # opclass 수정으로 email/name 단독 Index-Only 쿼리는 인덱스를 타지만,
    # list_users의 실제 쿼리(다중 컬럼 SELECT + email/name OR 조건)는
    # pgroonga가 일반 Index Scan과 Bitmap Scan을 지원하지 않아 여전히
    # Seq Scan으로 동작함. users 테이블 규모(관리자/직원 계정, 대용량
    # 아님)를 고려하면 현재는 문제 없음. 테이블 규모가 크게 늘어나거나
    # 검색 성능이 실제 이슈가 되면 btree+pg_trgm 등 대안 검토 필요.
    op.execute(
        "CREATE INDEX ix_users_email_pgroonga ON users "
        "USING pgroonga (email pgroonga_varchar_full_text_search_ops_v2)"
    )
    op.execute(
        "CREATE INDEX ix_users_name_pgroonga ON users "
        "USING pgroonga (name pgroonga_varchar_full_text_search_ops_v2)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_users_email_pgroonga")
    op.execute("DROP INDEX ix_users_name_pgroonga")
    op.execute("CREATE INDEX ix_users_email_pgroonga ON users USING pgroonga (email)")
    op.execute("CREATE INDEX ix_users_name_pgroonga ON users USING pgroonga (name)")
