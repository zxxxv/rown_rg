"""대목 벡터의 적재와 조회 — 색인 때 만들고 대조 때 읽는다.

왜 저장하나. 근거 대조에서 한글 주장과 영문 대목은 어휘 겹침이 원리적으로 0이다
(공유 문자가 없다). 임베딩은 그 일을 하지만, 화면에서 볼 때마다 대목을 임베딩하면
절당 1.56초가 든다(실측 2026-08-27: 절당 154건). 원격 임베딩 클라이언트는 캐시를
일부러 안 두므로 재계산이 매번 전액이라, 이 경로는 켤 수가 없었다.

대목은 청크에 딸린 고정된 것이라 색인 시점에 한 번 만들면 된다. 주장은 사람이 고쳐
쓰는 것이라 그때그때 임베딩한다 — 절당 154건 중 대목이 ~123건, 주장이 ~31건이라
8할이 조회로 바뀐다.

**모든 실패는 비치명이다.** 벡터가 없으면 대조가 종전대로 "직접 확인하세요"로 남는다.
색인이 벡터 때문에 실패하면 자료 자체가 안 들어오므로, 적재는 예외를 삼키고 로그만 남긴다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import structlog
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.clients.embedding_client import EmbeddingClient
from src.core.config import settings
from src.db.models.chunk_span_vector import ChunkSpanVector
from src.services.qa.alignment import _spans

logger = structlog.get_logger(__name__)

# 한 번에 임베딩할 대목 수 — 청크 하나가 6.7개라 자료 한 건(청크 38개)이 250개쯤 된다.
_EMBED_BATCH = 256


def space_id() -> str:
    """어느 임베딩 공간에서 만든 벡터인가.

    로컬은 모델 디렉터리 이름을 쓴다 - int8/fp16 폴더가 갈리므로 dtype까지 구분된다
    (EmbeddingCache의 지문과 같은 규약). 원격은 하나로 묶는다 - 원격 모델 교체는
    청크 벡터까지 어긋나게 하므로 어차피 전량 재색인을 부르는 사건이다.
    """
    if settings.embedding_remote_url:
        return "remote"
    return Path(settings.embedding_model_path).name[:64]


def content_hash(text: str) -> str:
    """청크 본문 지문 — 본문이 바뀌면 대목 오프셋이 무의미해진다."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


async def store_for_chunks(
    session: AsyncSession,
    chunks: list[tuple[UUID, str]],
    *,
    client: EmbeddingClient,
) -> int:
    """청크들의 대목 벡터를 만들어 넣는다. 넣은 개수를 돌려준다.

    같은 청크의 옛 행은 지우고 새로 넣는다 - 세대를 쌓지 않는다. 커밋은 호출자 몫이다.
    """
    space = space_id()
    plan: list[tuple[UUID, str, int, int, str]] = []
    for chunk_id, content in chunks:
        digest = content_hash(content)
        for start, end, text in _spans(content or ""):
            plan.append((chunk_id, digest, start, end, text))
    if not plan:
        return 0

    vectors: list[list[float]] = []
    for at in range(0, len(plan), _EMBED_BATCH):
        batch = plan[at : at + _EMBED_BATCH]
        results = await client.embed_batch([x[4] for x in batch])
        vectors.extend(r.embedding for r in results)
    if len(vectors) != len(plan):  # 계약 위반 - 짝이 어긋난 채로 저장하면 조용히 틀린다
        logger.warning("span_vectors.count_mismatch", wanted=len(plan), got=len(vectors))
        return 0

    await session.execute(
        delete(ChunkSpanVector).where(ChunkSpanVector.chunk_id.in_([c for c, _ in chunks]))
    )
    # ORM 객체로 넣으면 행마다 INSERT를 날려 행당 45ms가 든다(실측 2026-08-27:
    # 873행 39.8초). 같은 일을 대량 삽입은 587건/초로 한다 - 27배다. 여기는 색인
    # 경로라 자료 한 건이 몇 초 더 걸리는 게 그대로 사용자 대기가 된다.
    await session.execute(
        insert(ChunkSpanVector),
        [
            {
                "chunk_id": chunk_id,
                "span_start": start,
                "span_end": end,
                "embedding": vec,
                "space": space,
                "content_hash": digest,
            }
            for (chunk_id, digest, start, end, _), vec in zip(plan, vectors, strict=True)
        ],
    )
    return len(plan)


async def load_for_chunks(
    session: AsyncSession,
    chunk_texts: dict[UUID, str],
) -> dict[tuple[UUID, int], list[float]]:
    """(청크, 시작위치) → 벡터. 본문 지문이나 공간이 다른 행은 버린다.

    버리는 게 맞다 - 본문이 바뀌었으면 오프셋이 다른 글을 가리키고, 공간이 다르면
    거리가 뜻을 잃는다. 없으면 호출자가 그 대목만 그 자리에서 임베딩한다.
    """
    if not chunk_texts:
        return {}
    space = space_id()
    rows = (
        await session.execute(
            select(
                ChunkSpanVector.chunk_id,
                ChunkSpanVector.span_start,
                ChunkSpanVector.embedding,
                ChunkSpanVector.content_hash,
            ).where(
                ChunkSpanVector.chunk_id.in_(list(chunk_texts)),
                ChunkSpanVector.space == space,
            )
        )
    ).all()
    want = {cid: content_hash(text) for cid, text in chunk_texts.items()}
    out: dict[tuple[UUID, int], list[float]] = {}
    stale = 0
    for chunk_id, start, embedding, digest in rows:
        if want.get(chunk_id) != digest:
            stale += 1
            continue
        out[(chunk_id, start)] = list(embedding)
    if stale:
        logger.info("span_vectors.stale_skipped", n=stale, n_chunks=len(chunk_texts))
    return out


async def store_quietly(
    session_maker,
    chunks: list[tuple[UUID, str]],
    *,
    client: EmbeddingClient,
) -> int:
    """색인이 부르는 문 — 실패해도 색인을 막지 않는다.

    대목 벡터는 있으면 좋은 것이지 자료가 들어오는 조건이 아니다. 여기서 터뜨리면
    임베딩 서비스가 잠깐 흔들렸다는 이유로 자료 수집이 통째로 실패한다.
    """
    try:
        async with session_maker() as session:
            n = await store_for_chunks(session, chunks, client=client)
            await session.commit()
        logger.info("span_vectors.stored", n_spans=n, n_chunks=len(chunks))
        return n
    except Exception:
        logger.warning("span_vectors.store_failed", n_chunks=len(chunks), exc_info=True)
        return 0


async def backfill(limit: int | None = None, *, batch: int = 40) -> int:
    """이미 색인된 청크에 대해 한 번 돌린다 — 배포 뒤 1회.

    이미 있는 청크는 건너뛰므로 중간에 끊겨도 다시 돌리면 이어진다. 배제된 청크는
    검색에 안 나와 근거가 될 일이 없으므로 대상이 아니다.

    실측(2026-08-27): 임베딩 71건/초·대량삽입 587건/초 → 합쳐 ~60건/초.
    청크 13,528건 × 대목 6.7개 ≈ 90,000 벡터라 25분쯤 걸린다.
    """
    import time

    from src.clients.embedding_factory import get_embedding_client
    from src.db.models.chunk import Chunk
    from src.db.session import async_session_maker

    client = get_embedding_client()
    space = space_id()
    async with async_session_maker() as session:
        done = set(
            (
                await session.execute(
                    select(ChunkSpanVector.chunk_id)
                    .distinct()
                    .where(ChunkSpanVector.space == space)
                )
            )
            .scalars()
            .all()
        )
        rows = [
            (cid, content)
            for cid, content in (
                await session.execute(
                    select(Chunk.id, Chunk.content)
                    .where(Chunk.metadata_["excluded"].astext.is_(None))
                    .order_by(Chunk.id)
                )
            ).all()
            if cid not in done
        ]
    if limit is not None:
        rows = rows[:limit]
    logger.info("span_vectors.backfill.start", pending=len(rows), already=len(done), space=space)

    started = time.perf_counter()
    total = 0
    for at in range(0, len(rows), batch):
        async with async_session_maker() as session:
            total += await store_for_chunks(session, rows[at : at + batch], client=client)
            await session.commit()
        if (at // batch) % 25 == 0:
            logger.info(
                "span_vectors.backfill.progress",
                chunks=min(at + batch, len(rows)),
                of=len(rows),
                vectors=total,
                elapsed_s=round(time.perf_counter() - started),
            )
    logger.info(
        "span_vectors.backfill.done",
        vectors=total,
        chunks=len(rows),
        elapsed_s=round(time.perf_counter() - started),
    )
    return total


if __name__ == "__main__":  # python -m src.services.qa.span_vectors [limit]
    import asyncio
    import sys

    asyncio.run(backfill(int(sys.argv[1]) if len(sys.argv) > 1 else None))
