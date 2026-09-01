"""대목 벡터 — 청크 **안의** 한 대목을 임베딩해 둔 것.

근거 대조에서 어휘 겹침은 한글 주장과 영문 대목 사이에 0점을 준다(공유 문자가 없다).
같은 일을 임베딩은 한다 — 검색이 이미 그 임베딩으로 영문 청크를 잘 집어온다. 문제는
대목을 **볼 때마다** 임베딩하면 절당 1.56초가 든다는 것이었다(실측 2026-08-27,
절당 154건). 원격 임베딩 클라이언트는 캐시를 일부러 안 두므로 재계산이 매번 전액이다.

그래서 색인 시점에 한 번 만들어 여기 둔다. 조회로 바뀌면 절당 지연의 8할이 사라진다.

**본문 지문에 매어 둔다.** 오프셋은 청크 본문에 대한 좌표라, 본문이 바뀌면 벡터가
아니라 좌표가 먼저 무의미해진다. 지문이 다른 행은 읽는 쪽에서 그냥 안 쓴다 - 지울
필요가 없고, 다시 만들면 덮인다. 같은 이유로 임베딩 공간(space)도 함께 적는다.
원격 모델이 바뀌면 옛 벡터는 다른 공간에 있으므로 섞으면 조용히 틀린다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ChunkSpanVector(Base):
    __tablename__ = "chunk_span_vectors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1024), nullable=False)
    # 어느 임베딩 공간에서 만든 벡터인가 — 모델이 바뀌면 옛것을 안 쓴다.
    space: Mapped[str] = mapped_column(String(64), nullable=False)
    # 청크 본문 지문 — 본문이 바뀌면 오프셋이 무의미해지므로 읽는 쪽이 거른다.
    content_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # 한 청크의 한 대목은 한 벌만 — 다시 만들면 지우고 넣는다(세대를 안 쌓는다).
        UniqueConstraint("chunk_id", "span_start", name="chunk_span_vectors_chunk_start_key"),
        # 읽기는 늘 "이 청크들의 대목 전부"라 chunk_id 단독 색인이면 족하다.
        # 근사 최근접(ivfflat/hnsw)은 안 만든다 — 후보가 청크 안 몇십 개뿐이라
        # 전수 비교가 더 빠르고, 색인이 있으면 적재만 느려진다.
        Index("ix_chunk_span_vectors_chunk_id", "chunk_id"),
    )
