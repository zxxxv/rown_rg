"""create quota_settings and quota_settings_history tables

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 기존 config 상수(Settings.org_monthly_cost_limit_usd, core.limit.DEFAULT_ROLE_LIMIT_USD)의
# 실제 기본값을 그대로 이관하는 초기 시딩 데이터. updated_by는 관리자가 아직 수정한 적이
# 없으므로 전부 null(컬럼 목록에서 생략 -> DB가 NULL로 채운다).
_SEED_VALUES: dict[str, str] = {
    "ORG_MONTHLY_COST_LIMIT_USD": "3000",
    "DEFAULT_LIMIT_SUPER_ADMIN_USD": "500",
    "DEFAULT_LIMIT_ADMIN_USD": "300",
    "DEFAULT_LIMIT_WORKER_USD": "200",
    "DEFAULT_LIMIT_VIEWER_USD": "50",
}

_quota_settings_table = sa.table(
    "quota_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.String),
)


def upgrade() -> None:
    op.create_table(
        "quota_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_quota_settings_key"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_quota_settings_key", "quota_settings", ["key"])

    op.create_table(
        "quota_settings_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("old_value", sa.String(255), nullable=True),
        sa.Column("new_value", sa.String(255), nullable=False),
        sa.Column(
            "changed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
    )
    op.create_index("ix_quota_settings_history_key", "quota_settings_history", ["key"])

    op.bulk_insert(
        _quota_settings_table,
        [{"key": key, "value": value} for key, value in _SEED_VALUES.items()],
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM quota_settings WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": list(_SEED_VALUES.keys())},
    )

    op.drop_index("ix_quota_settings_history_key", table_name="quota_settings_history")
    op.drop_table("quota_settings_history")

    op.drop_index("ix_quota_settings_key", table_name="quota_settings")
    op.drop_table("quota_settings")
