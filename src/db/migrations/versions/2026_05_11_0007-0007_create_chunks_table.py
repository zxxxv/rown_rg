"""create chunks table

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column(
            "track",
            sa.VARCHAR(20),
            sa.CheckConstraint(
                "track IN ('content', 'style')",
                name="ck_chunks_track",
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["project_sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_chunks_project_track", "chunks", ["project_id", "track"])
    op.create_index("ix_chunks_track", "chunks", ["track"])
    op.create_index(
        "ix_chunks_metadata",
        "chunks",
        ["metadata"],
        postgresql_using="gin",
    )
    # HNSW 벡터 인덱스
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    # pgroonga 한국어 BM25 인덱스
    op.execute(
        "CREATE INDEX ix_chunks_content_pgroonga ON chunks "
        "USING pgroonga (content)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_pgroonga")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.drop_index("ix_chunks_metadata", table_name="chunks")
    op.drop_index("ix_chunks_track", table_name="chunks")
    op.drop_index("ix_chunks_project_track", table_name="chunks")
    op.drop_table("chunks")
