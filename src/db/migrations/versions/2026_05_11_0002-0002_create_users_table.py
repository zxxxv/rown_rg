"""create users table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", sa.VARCHAR(255), nullable=False),
        sa.Column("password_hash", sa.VARCHAR(255), nullable=False),
        sa.Column("name", sa.VARCHAR(100), nullable=False),
        sa.Column(
            "role",
            sa.VARCHAR(20),
            sa.CheckConstraint(
                "role IN ('super_admin', 'admin', 'worker', 'viewer')",
                name="ck_users_role",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("totp_secret", sa.VARCHAR(32), nullable=True),
        sa.Column("last_login_at", sa.TIMESTAMP(), nullable=True),
        sa.Column(
            "failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("locked_until", sa.TIMESTAMP(), nullable=True),
        sa.Column(
            "password_changed_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_is_active", "users", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_users_is_active", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
