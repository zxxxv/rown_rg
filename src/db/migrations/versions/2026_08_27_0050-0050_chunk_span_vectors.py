"""0050 대목 벡터 보관 — 볼 때마다 다시 만들던 것을 색인 때 한 번 만든다.

근거 대조에서 한글 주장과 영문 대목은 어휘 겹침이 원리적으로 0이다(공유 문자가 없다).
임베딩은 그걸 하지만, 대목을 화면에서 볼 때마다 임베딩하면 절당 1.56초가 든다
(실측 2026-08-27: 절당 154건, 그중 8할이 대목이고 2할이 주장). 원격 임베딩
클라이언트는 캐시를 일부러 안 두므로 재계산이 매번 전액이다.

대목 벡터를 색인 시점에 만들어 두면 그 8할이 조회로 바뀐다. 규모는 작다 —
청크 13,528건 × 대목 6.7개 ≈ 90,299 벡터, fp32로 0.34GB, 초기 적재 15분(99건/초).

본문 지문(content_hash)과 공간(space)을 함께 적는다. 오프셋은 청크 본문에 대한
좌표라 본문이 바뀌면 벡터보다 좌표가 먼저 무의미해지고, 임베딩 모델이 바뀌면 옛
벡터는 다른 공간에 있다. 둘 다 읽는 쪽에서 거른다 — 지울 필요가 없고 다시 만들면 덮인다.

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk_span_vectors",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=False),
        sa.Column("span_end", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(1024), nullable=False),
        sa.Column("space", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "span_start", name="chunk_span_vectors_chunk_start_key"),
    )
    op.create_index("ix_chunk_span_vectors_chunk_id", "chunk_span_vectors", ["chunk_id"])
    # 근사 최근접 색인(ivfflat/hnsw)은 만들지 않는다 — 조회는 늘 "이 청크의 대목
    # 전부"라 후보가 수십 개뿐이고, 그 규모에서는 전수 비교가 더 빠르다. 색인이
    # 있으면 적재만 느려진다.


def downgrade() -> None:
    op.drop_index("ix_chunk_span_vectors_chunk_id", table_name="chunk_span_vectors")
    op.drop_table("chunk_span_vectors")
