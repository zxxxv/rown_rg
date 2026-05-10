"""create ip_whitelist table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ip_whitelist",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("ip_cidr", sa.dialects.postgresql.CIDR(), nullable=False),
        sa.Column("description", sa.VARCHAR(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_ip_whitelist_ip_cidr", "ip_whitelist", ["ip_cidr"])
    op.create_index(
        "ix_ip_whitelist_active_expires", "ip_whitelist", ["is_active", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ip_whitelist_active_expires", table_name="ip_whitelist")
    op.drop_index("ix_ip_whitelist_ip_cidr", table_name="ip_whitelist")
    op.drop_table("ip_whitelist")
