"""create project_sources table

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_sources",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("library_node_id", sa.UUID(), nullable=True),
        sa.Column("upload_path", sa.Text(), nullable=True),
        sa.Column(
            "source_type",
            sa.VARCHAR(20),
            sa.CheckConstraint(
                "source_type IN ('library', 'upload', 'web_search')",
                name="ck_project_sources_source_type",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.VARCHAR(500), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "reliability",
            sa.VARCHAR(10),
            sa.CheckConstraint(
                "reliability IN ('high', 'medium', 'low')",
                name="ck_project_sources_reliability",
            ),
            nullable=True,
        ),
        sa.Column("is_included", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["library_node_id"], ["library_nodes.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_project_sources_project_id", "project_sources", ["project_id"])
    op.create_index("ix_project_sources_is_included", "project_sources", ["is_included"])
    op.create_index("ix_project_sources_source_type", "project_sources", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_project_sources_source_type", table_name="project_sources")
    op.drop_index("ix_project_sources_is_included", table_name="project_sources")
    op.drop_index("ix_project_sources_project_id", table_name="project_sources")
    op.drop_table("project_sources")
