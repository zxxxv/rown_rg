"""create projects table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("title", sa.VARCHAR(255), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("preset", sa.VARCHAR(100), nullable=True),
        sa.Column("config", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "status",
            sa.VARCHAR(20),
            sa.CheckConstraint(
                "status IN ('created', 'researching', 'indexing', 'writing', "
                "'reviewing', 'completed', 'archived')",
                name="ck_projects_status",
            ),
            server_default=sa.text("'created'"),
            nullable=False,
        ),
        sa.Column(
            "depth_mode",
            sa.VARCHAR(20),
            sa.CheckConstraint(
                "depth_mode IN ('outline_only', 'standard', 'full_report', 'deep_dive')",
                name="ck_projects_depth_mode",
            ),
            server_default=sa.text("'full_report'"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index(
        "ix_projects_config",
        "projects",
        ["config"],
        postgresql_using="gin",
    )
    op.create_index("ix_projects_created_at", "projects", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_projects_created_at", table_name="projects")
    op.drop_index("ix_projects_config", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")
