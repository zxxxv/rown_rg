"""섹션 단위 검색 어댑터 — retrieval 백엔드(SearchHit)를 core RetrievedChunk로 변환.

write 루프는 SectionPlan 하나를 받아 근거 청크를 돌려주는 SectionRetriever만 알면
된다(실검색이든 테스트 주입이든). make_section_retriever로 프로젝트·검색기에 바인딩한다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from src.core.types import RetrievedChunk, SectionPlan
from src.services.retrieval.base import SearchClient, SearchHit, Track

if TYPE_CHECKING:
    from src.clients.reranker_client import RerankerClient
    from src.services.retrieval._multilingual import QueryTranslator
    from src.services.retrieval._raptor import SummaryFetcher

logger = structlog.get_logger(__name__)

# 섹션 하나 → 근거 청크. write 루프가 의존하는 유일한 검색 인터페이스.
SectionRetriever = Callable[[SectionPlan], Awaitable[list[RetrievedChunk]]]

DEFAULT_TOP_K = 10
# 리랭커 사용 시 1차 검색 폭 — 넓게 가져와 cross-encoder가 top_k로 추리게 한다.
DEFAULT_FETCH_K = 30


def hit_to_chunk(hit: SearchHit) -> RetrievedChunk:
    """검색 백엔드 SearchHit을 생성·게이트가 쓰는 RetrievedChunk로 축약."""
    header = hit.metadata.get("header_path") if isinstance(hit.metadata, dict) else None
    return RetrievedChunk(
        chunk_id=hit.chunk_id,
        source_id=hit.source_id,
        content=hit.content,
        score=hit.score,
        header_path=[str(h) for h in header] if isinstance(header, list) else [],
    )


async def _merged_with_translation(
    hits: list[SearchHit],
    translate: QueryTranslator,
    query: str,
    *,
    context: str,
    client: SearchClient,
    project_id: UUID,
    track: Track,
    width: int,
) -> list[SearchHit]:
    """번역 질의로 한 번 더 검색해 순위를 합친다. 실패는 원 결과 그대로.

    한글 질의로는 영문 청크가 dense 상위에 아예 안 올라온다(실측 0/20). 색인을 이중으로
    만들지 않고 질의 쪽에서 푸는 방식이라 원문 대조가 그대로 유지된다.
    """
    try:
        translated = await translate(query, context)
        if not translated or translated == query:
            return hits
        second = await client.search(translated, project_id, track, width)
        if not second:
            return hits
        from src.services.retrieval._multilingual import merge_rankings

        merged = merge_rankings(hits, second)
    except Exception:
        logger.warning("retrieval.translate_merge_failed", project_id=str(project_id))
        return hits
    logger.info(
        "retrieval.translated",
        project_id=str(project_id),
        query=query,
        translated=translated,
        added=len(merged) - len(hits),
    )
    return merged[: max(width, len(hits))]


def _section_query(section: SectionPlan) -> str:
    """1차 검색 쿼리 — 절 제목만. pgroonga &@~가 공백 항을 AND로 묶으므로
    여기에 주제 문장을 붙이면 키워드 재현율이 무너진다(짧게 유지)."""
    return section.title


def _rerank_query(section: SectionPlan, topic: str | None) -> str:
    """재채점·요약 검색용 주제 앵커 쿼리 — '주제 — 절 제목 — 작성 방향'.

    절 제목만으로 재채점하면 '시장 규모' 같은 일반 제목이 주제와 무관한 청크를
    상위로 올린다(2026-08-03 주제 표류 실측: 원격근무 보고서에 공유오피스 시장
    청크가 대량 유입). cross-encoder에 주제를 결합하면 이탈 청크가 채점 단계에서
    밀려난다 — 1차 검색(재현율)은 건드리지 않는 최소 침습 지점.
    """
    parts = [topic or "", section.title, section.direction or ""]
    return " — ".join(p.strip() for p in parts if p and p.strip())


async def retrieve_for_section(
    section: SectionPlan,
    *,
    client: SearchClient,
    project_id: UUID,
    track: Track = "content",
    top_k: int = DEFAULT_TOP_K,
    reranker: RerankerClient | None = None,
    fetch_k: int = DEFAULT_FETCH_K,
    summary_fetcher: SummaryFetcher | None = None,
    topic: str | None = None,
    translate: QueryTranslator | None = None,
) -> list[RetrievedChunk]:
    """한 섹션의 근거 청크를 검색해 RetrievedChunk 리스트로 반환.

    reranker가 주어지면 넓게(fetch_k) 검색한 뒤 cross-encoder로 재채점해 top_k로
    줄인다. 1차 검색은 절 제목(재현율), 재채점·요약 검색은 주제 앵커 쿼리
    (_rerank_query)를 쓴다 — 주제 이탈 청크를 채점 단계에서 걸러낸다.

    summary_fetcher(RAPTOR)가 있으면 요약 노드(is_summary=True)를 뒤에 덧붙인다 —
    리랭킹 대상이 아니며, 후보 생성에서 인용 불가 배경 맥락으로만 쓰인다.
    실패는 삼킨다(맥락 부재가 검색 실패가 되어선 안 된다).
    """
    query = _section_query(section)
    anchored = _rerank_query(section, topic)
    width = max(fetch_k, top_k) if reranker is not None else top_k
    hits = await client.search(query, project_id, track, width)
    if translate is not None:
        hits = await _merged_with_translation(
            hits,
            translate,
            query,
            # 번역기에 넘길 문맥 — 제목만 주면 '시장 규모'가 무엇의 시장인지 모른다.
            context=anchored,
            client=client,
            project_id=project_id,
            track=track,
            width=width,
        )
    if reranker is not None:
        # lazy import: _reranking → reranker_client 체인의 무거운 의존을 사용 시점으로 미룬다.
        from src.services.retrieval._reranking import rerank_hits

        hits = await rerank_hits(reranker, anchored, hits, top_k=top_k)
    else:
        hits = hits[:top_k]
    chunks = [hit_to_chunk(h) for h in hits]
    if summary_fetcher is not None:
        try:
            chunks.extend(await summary_fetcher(anchored))
        except Exception:
            logger.warning("retrieval.summary_fetch_failed", project_id=str(project_id))
    return chunks


def make_section_retriever(
    client: SearchClient,
    project_id: UUID,
    *,
    track: Track = "content",
    top_k: int = DEFAULT_TOP_K,
    reranker: RerankerClient | None = None,
    fetch_k: int = DEFAULT_FETCH_K,
    summary_fetcher: SummaryFetcher | None = None,
    topic: str | None = None,
    translate: QueryTranslator | None = None,
) -> SectionRetriever:
    """프로젝트·검색기에 바인딩된 SectionRetriever를 만든다 (write 루프 주입용)."""

    async def _retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        # 다관점 절(에이전트 2개 이상)은 다룰 축이 늘어 분량 목표도 커진다 — 재료를
        # 같이 늘리지 않으면 파트당 근거가 배정 최소치(3개) 아래로 떨어져 파트가
        # 병합되고, 결국 목표만 크고 쓸 거리는 없는 상태가 된다(2026-08-09).
        k = top_k * max(1, len(section.analysts))
        chunks = await retrieve_for_section(
            section,
            client=client,
            project_id=project_id,
            track=track,
            top_k=k,
            reranker=reranker,
            fetch_k=max(fetch_k, k * 2),
            summary_fetcher=summary_fetcher,
            topic=topic,
            translate=translate,
        )
        return await _with_source_titles(chunks)

    return _retrieve


async def _with_source_titles(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """근거에 자료 제목을 채운다 - 프롬프트가 '무엇의 어디'를 함께 보여주기 위함.

    절당 쿼리 1회. 실패는 비치명 - 제목이 없어도 근거 본문은 그대로 쓴다.
    """
    ids = {c.source_id for c in chunks if c.source_id}
    if not ids:
        return chunks
    try:
        from sqlalchemy import select

        from src.db.models.project_source import ProjectSource
        from src.db.session import async_session_maker

        async with async_session_maker() as session:
            rows = (
                await session.execute(
                    select(ProjectSource.id, ProjectSource.title, ProjectSource.url).where(
                        ProjectSource.id.in_(ids)
                    )
                )
            ).all()
        titles = {sid: (title or url or "") for sid, title, url in rows}
    except Exception:
        logger.warning("section_retriever.titles_failed", exc_info=True)
        return chunks
    return [
        c.model_copy(update={"source_title": titles.get(c.source_id, "")})
        if titles.get(c.source_id)
        else c
        for c in chunks
    ]
