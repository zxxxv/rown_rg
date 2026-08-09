"""create sections table (persist completed report sections)

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("section_number", sa.Integer(), nullable=False),
        sa.Column("chapter_title", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("level", sa.Integer(), server_default="2", nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "source_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("qa_status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "project_id", "chapter_number", "section_number", name="uq_section_pos"
        ),
        sa.CheckConstraint(
            "qa_status IN ('passed', 'failed', 'pending')", name="sections_qa_status_check"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'writing', 'completed', 'failed')",
            name="sections_status_check",
        ),
    )
    op.create_index("ix_sections_project_id", "sections", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_sections_project_id", table_name="sections")
    op.drop_table("sections")
