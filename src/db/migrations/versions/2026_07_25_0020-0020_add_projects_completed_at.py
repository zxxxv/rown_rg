"""add completed_at column and index on projects

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_projects_completed_at", "projects", ["completed_at"])


def downgrade() -> None:
    op.drop_index("ix_projects_completed_at", table_name="projects")
    op.drop_column("projects", "completed_at")
