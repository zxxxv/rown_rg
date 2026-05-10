"""create library_nodes table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "library_nodes",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.VARCHAR(255), nullable=False),
        sa.Column(
            "type",
            sa.VARCHAR(10),
            sa.CheckConstraint("type IN ('folder', 'file')", name="ck_library_nodes_type"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.VARCHAR(100), nullable=True),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "visible_to_users", ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column(
            "visible_to_roles", ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["library_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_library_nodes_parent_id", "library_nodes", ["parent_id"])
    op.create_index("ix_library_nodes_type", "library_nodes", ["type"])
    op.create_index(
        "ix_library_nodes_metadata",
        "library_nodes",
        ["metadata"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_library_nodes_visible_to_users",
        "library_nodes",
        ["visible_to_users"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_library_nodes_visible_to_roles",
        "library_nodes",
        ["visible_to_roles"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_library_nodes_visible_to_roles", table_name="library_nodes")
    op.drop_index("ix_library_nodes_visible_to_users", table_name="library_nodes")
    op.drop_index("ix_library_nodes_metadata", table_name="library_nodes")
    op.drop_index("ix_library_nodes_type", table_name="library_nodes")
    op.drop_index("ix_library_nodes_parent_id", table_name="library_nodes")
    op.drop_table("library_nodes")
