"""convert naive timestamp columns to timestamptz (UTC standard)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (테이블, 컬럼) — 모든 datetime 컬럼
COLUMNS: list[tuple[str, str]] = [
    ("users", "last_login_at"),
    ("users", "locked_until"),
    ("users", "password_changed_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
    ("projects", "created_at"),
    ("projects", "updated_at"),
    ("project_sources", "created_at"),
    ("chunks", "created_at"),
    ("library_nodes", "created_at"),
    ("library_nodes", "updated_at"),
    ("raptor_nodes", "created_at"),
    ("consistency_graph_nodes", "created_at"),
    ("consistency_graph_nodes", "updated_at"),
    ("token_usage", "created_at"),
    ("ip_whitelist", "expires_at"),
    ("ip_whitelist", "created_at"),
    ("ip_whitelist", "updated_at"),
]


def upgrade() -> None:
    # 기존 naive 값을 UTC로 간주하여 timestamptz로 변환
    for table, col in COLUMNS:
        op.alter_column(
            table,
            col,
            type_=sa.TIMESTAMP(timezone=True),
            existing_type=sa.TIMESTAMP(timezone=False),
            postgresql_using=f"{col} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    # timestamptz -> naive timestamp (UTC 기준의 naive 값으로 되돌림)
    for table, col in COLUMNS:
        op.alter_column(
            table,
            col,
            type_=sa.TIMESTAMP(timezone=False),
            existing_type=sa.TIMESTAMP(timezone=True),
            postgresql_using=f"{col} AT TIME ZONE 'UTC'",
        )
