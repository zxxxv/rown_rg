"""create raptor_nodes table

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raptor_nodes",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "chunk_ids", ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["raptor_nodes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_raptor_nodes_project_level", "raptor_nodes", ["project_id", "level"])
    op.create_index("ix_raptor_nodes_parent_id", "raptor_nodes", ["parent_id"])
    op.execute(
        "CREATE INDEX ix_raptor_nodes_embedding ON raptor_nodes "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_raptor_nodes_embedding")
    op.drop_index("ix_raptor_nodes_parent_id", table_name="raptor_nodes")
    op.drop_index("ix_raptor_nodes_project_level", table_name="raptor_nodes")
    op.drop_table("raptor_nodes")
