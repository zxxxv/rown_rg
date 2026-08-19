"""섹션 단위 검색 어댑터 — retrieval 백엔드(SearchHit)를 core RetrievedChunk로 변환.

write 루프는 SectionPlan 하나를 받아 근거 청크를 돌려주는 SectionRetriever만 알면
된다(실검색이든 테스트 주입이든). make_section_retriever로 프로젝트·검색기에 바인딩한다.
"""

from __future__ import annotations

import asyncio
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

# 절 하나가 던지는 1차 검색 질의 수 상한. 질의 하나당 검색 왕복이 하나 늘고, 병합 뒤
# 리랭커 입력은 width로 다시 캡되므로(=리랭킹 비용 불변) 늘어나는 건 검색 호출뿐이다.
MAX_SECTION_QUERIES = 6
# 절 하나에서 질의로 승격할 핵심 포인트 수. 앞쪽이 사용자가 중요하게 적은 순서다.
MAX_KEYPOINT_QUERIES = 2
# 에이전트 1종이 기여할 질의 수. 다관점 절에서 한 관점이 질의를 독식하지 않게 한다.
MAX_QUERIES_PER_ANALYST = 2


def hit_to_chunk(hit: SearchHit) -> RetrievedChunk:
    """검색 백엔드 SearchHit을 생성·게이트가 쓰는 RetrievedChunk로 축약."""
    meta = hit.metadata if isinstance(hit.metadata, dict) else {}
    header = meta.get("header_path")
    year = meta.get("published_year")
    return RetrievedChunk(
        chunk_id=hit.chunk_id,
        source_id=hit.source_id,
        content=hit.content,
        score=hit.score,
        header_path=[str(h) for h in header] if isinstance(header, list) else [],
        published_year=year if isinstance(year, int) else None,
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


# 재채점 앵커에 실을 주제문 상한(문자). topic은 사용자가 자유로 쓰는 지시문이라 길다 —
# 탄소규제 런은 383자(189토큰)였는데, 리랭커는 query를 절대 자르지 않고 passage만
# 자르므로(truncation="only_second", max_length=512) 근거 본문이 240~300토큰으로
# 잘려 나갔다(청크 중앙값 518자 ≈ 340토큰). 게다가 절마다 똑같은 189토큰 접두사가
# 붙어 절 간 변별력까지 지웠다. 표류 방지 역할은 이제 장 제목이 대신한다.
_TOPIC_ANCHOR_CHARS = 80
# 앵커 쿼리 전체 상한. 이 아래로 유지해야 cross-encoder 512 창에서 근거 본문 몫이 남는다.
_RERANK_QUERY_CHARS = 240


def _with_chapter(section: SectionPlan) -> str:
    """'장 제목 절 제목' — 장 제목이 절 제목에 이미 들어 있으면 절 제목만."""
    title = section.title.strip()
    chapter = section.chapter_title.strip()
    if chapter and chapter.lower() not in title.lower():
        return f"{chapter} {title}"
    return title


def section_search_query(section: SectionPlan) -> str:
    """1차 검색 쿼리 — '장 제목 + 절 제목'. 짧게 유지한다.

    1차는 재현율 담당이고 같은 문자열이 dense 임베딩에도 들어가므로, 핵심 포인트까지
    이어 붙이면 질의 벡터가 흐려진다. 변별에 필요한 최소한(장 맥락)만 더한다 —
    풍부한 맥락은 길이 예산이 넉넉한 재채점 쪽(_rerank_query)이 맡는다.

    절 제목만 쓰던 시절엔 비교형 목차(규제 4종을 같은 5절 틀로 보는 정석 설계)에서
    네 장의 같은 절이 **글자까지 같은 질의**를 던져 같은 근거를 받았다. 2026-08-14
    실측: 1.3과 2.3의 인용 자료 집합이 완전 일치했고, 2장이 EU CBAM 장인데 2.3~2.5가
    CBAM 자료를 놔두고 RE100 자료를 인용했다. 반대로 제목이 고유한 절(2.2 'EU CBAM
    상세 분석')은 자기 장 자료를 정확히 물어왔다 — 같은 런 안의 자연 실험이다.

    pgroonga는 공백을 AND로 묶지만 _keyword.to_or_query가 토큰을 OR로 바꾸고 순위는
    pgroonga_score에 맡긴다(2026-08-10) — 항이 늘어도 재현율은 안 떨어지고, 오히려
    장 제목까지 걸린 청크가 위로 온다. 다만 문장을 통째로 넣지는 않는다(잡음 토큰).
    """
    return _with_chapter(section)


def _analyst_queries(section: SectionPlan, topic: str | None, catalog: dict | None) -> list[str]:
    """이 절에 배정된 에이전트가 제공하는 검색 질의 — `{topic}` 치환.

    카탈로그의 AnalystSpec.queries는 계약이 적혀 있는데(loader.AnalystSpec 독스트링)
    소비하는 곳이 없어 값을 넣어도 아무 일이 없었다(2026-08-19 발견). 관점이 다르면
    찾아야 할 자료도 다르다는 게 에이전트 배정의 전제인데, 배정이 작성 프롬프트에만
    닿고 검색에는 안 닿고 있었다.

    시스템 카탈로그 질의에는 영어 표현이 섞여 있다(예: "{topic} STEEP analysis").
    한글 질의로는 영어 청크가 안 잡히던 BGE-M3 언어 편향에도 같이 듣는다.

    `{topic}` 자리에는 **보고서 주제가 아니라 장·절 제목**을 넣는다. 주제를 넣으면
    같은 에이전트를 배정받은 절들이 글자까지 같은 질의를 던져(예: 다섯 절이 모두
    "글로벌 탄소규제 동향 SWOT") 8/14에 고친 중복 질의 문제가 그대로 재발한다 —
    검색을 분화하려고 넣은 질의가 오히려 획일화를 만든다. 주제 앵커는 재채점
    질의(_rerank_query)가 이미 들고 있어 주제 표류는 그쪽에서 걸린다. 절 제목이
    비어 있을 때만 주제로 폴백한다.
    """
    if not catalog or not section.analysts:
        return []
    anchor = " ".join(_with_chapter(section).split()) or " ".join((topic or "").split())
    anchor = anchor[:_TOPIC_ANCHOR_CHARS]
    out: list[str] = []
    for name in section.analysts:
        spec = catalog.get(name)
        if spec is None:
            continue
        for template in list(getattr(spec, "queries", []) or [])[:MAX_QUERIES_PER_ANALYST]:
            if not isinstance(template, str) or not template.strip():
                continue
            # 주제가 없으면 자리표시자를 지운다 — "{topic} SWOT"이 그대로 나가면
            # 중괄호가 키워드 토큰이 되어 잡음만 는다.
            text = (
                template.replace("{topic}", anchor) if anchor else template.replace("{topic}", "")
            )
            text = " ".join(text.split())
            if text:
                out.append(text)
    return out


def section_query_set(
    section: SectionPlan, topic: str | None = None, catalog: dict | None = None
) -> list[str]:
    """1차 검색 질의 집합 — 재현율은 한 각도가 아니라 여러 각도에서 나온다.

    질의 하나(장+절 제목)만 던지던 구조에서는 절이 무엇을 다루든 같은 자료가 왔다.
    핵심 포인트와 담당 에이전트 관점을 **각각 독립 질의로** 올린다 — 한 질의에 모두
    이어 붙이면 dense 질의 벡터가 흐려진다(그래서 기본 질의는 여전히 짧게 둔다).

    순서가 우선순위다: 기본(재현율 담당) → 핵심 포인트 → 에이전트 관점. 상한에
    걸려 잘리는 쪽이 뒤가 되도록 둔다. 중복은 제거한다.
    """
    from src.core.config import settings

    base = section_search_query(section)
    if not settings.retrieval_multi_query_enabled:
        return [base]
    queries = [base]
    title = _with_chapter(section)
    for point in section.key_points[:MAX_KEYPOINT_QUERIES]:
        text = " ".join((point or "").split())
        if not text:
            continue
        # 핵심 포인트만 단독으로 던지면 '관련 법률' 같은 일반어가 주제를 벗어난다 —
        # 절 맥락을 앞에 붙여 묶되 짧게 유지한다.
        queries.append(f"{title} {text}" if text not in title else title)
    queries.extend(_analyst_queries(section, topic, catalog))

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique[:MAX_SECTION_QUERIES]


def interleave_by_query(ranked_lists: list[list[SearchHit]], limit: int) -> list[SearchHit]:
    """질의별 쿼터를 보장하는 라운드로빈 병합 — 각 관점이 제 몫을 갖는다.

    처음엔 RRF(Reciprocal Rank Fusion)를 썼는데 **반대로 작동했다**. RRF는 여러
    리스트가 공통으로 위에 올린 항목을 끌어올리는 합의 융합이라(원 용도가 dense+BM25
    융합이다), 질의를 늘리면 '모든 질의에 두루 걸리는 중심 청크'가 상위를 먹고 한
    관점만 찾아온 청크는 나머지 질의에서 순위가 없어 밀린다. v2 탄소규제 색인 실측
    (2026-08-19, 20절): 절쌍 평균 자카드 0.243 → 0.319(수렴), 영어 청크 비율
    50.6% → 36.0%. 관점을 넓히려고 넣은 질의가 오히려 획일화를 만들었다.

    라운드로빈은 순위 1위끼리, 2위끼리 차례로 걷어 간다 — 질의 하나가 혼자 찾아온
    자료도 자기 차례에 반드시 들어온다. 앞쪽 질의(기본=재현율 담당)가 먼저 걷힌다.

    반환 hit의 score는 원 검색 점수 중 최댓값을 유지한다 — 뒤에 붙는 리랭커가 다시
    채점하므로 여기 점수는 폴백(리랭커 off)일 때만 순서에 쓰인다.
    """
    best: dict[UUID, SearchHit] = {}
    for hits in ranked_lists:
        for hit in hits:
            prev = best.get(hit.chunk_id)
            if prev is None or hit.score > prev.score:
                best[hit.chunk_id] = hit

    picked: list[SearchHit] = []
    seen: set[UUID] = set()
    depth = max((len(h) for h in ranked_lists), default=0)
    for rank in range(depth):
        for hits in ranked_lists:
            if rank >= len(hits):
                continue
            cid = hits[rank].chunk_id
            if cid in seen:
                continue
            seen.add(cid)
            picked.append(best[cid])
            if len(picked) >= limit:
                return picked
    return picked


def _topic_anchor(topic: str | None) -> str:
    """주제문에서 앵커로 쓸 앞머리만. 줄바꿈은 공백으로 눕힌다."""
    text = " ".join((topic or "").split())
    if len(text) <= _TOPIC_ANCHOR_CHARS:
        return text
    return text[:_TOPIC_ANCHOR_CHARS].rstrip() + "…"


def _rerank_query(section: SectionPlan, topic: str | None) -> str:
    """재채점·요약 검색용 앵커 쿼리 — 변별력 큰 순으로 잇고 총량을 캡한다.

    절 제목만으로 재채점하면 '시장 규모' 같은 일반 제목이 주제와 무관한 청크를
    상위로 올린다(2026-08-03 주제 표류 실측: 원격근무 보고서에 공유오피스 시장
    청크가 대량 유입). 그때 주제문을 통째로 앞에 붙였는데, 그 처방이 길이 문제와
    획일화 문제를 함께 만들었다(위 _TOPIC_ANCHOR_CHARS 주석).

    순서가 곧 우선순위다 — 캡에 걸려 잘리는 쪽이 변별력 낮은 주제문이 되도록
    장·절 제목을 앞에 둔다. 핵심 포인트는 1차 검색이 아니라 여기에만 싣는다
    (cross-encoder는 질의를 안 자르고, dense 질의처럼 벡터가 흐려지지도 않는다).
    """
    parts = [
        _with_chapter(section),
        section.direction,
        " ".join(section.key_points[:2]),
        _topic_anchor(topic),
    ]
    joined: list[str] = []
    for part in parts:
        text = " ".join((part or "").split())
        # 이미 실린 말을 또 싣지 않는다(검색 질의가 절 제목과 겹치는 흔한 경우).
        if text and all(text not in prev for prev in joined):
            joined.append(text)
    return " — ".join(joined)[:_RERANK_QUERY_CHARS]


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
    analyst_catalog: dict | None = None,
) -> list[RetrievedChunk]:
    """한 섹션의 근거 청크를 검색해 RetrievedChunk 리스트로 반환.

    reranker가 주어지면 넓게(fetch_k) 검색한 뒤 cross-encoder로 재채점해 top_k로
    줄인다. 1차 검색은 절 질의 집합(section_query_set — 기본 제목 + 핵심 포인트 +
    에이전트 관점)을 각각 던져 RRF로 병합하고, 재채점·요약 검색은 주제 앵커 쿼리
    (_rerank_query)를 쓴다 — 주제 이탈 청크를 채점 단계에서 걸러낸다.

    analyst_catalog가 없으면 질의는 기본 하나로 줄어든다(옛 동작) — 카탈로그를 못
    읽는 호출부(테스트·미리보기)에서도 검색 자체는 정상 동작해야 한다.

    summary_fetcher(RAPTOR)가 있으면 요약 노드(is_summary=True)를 뒤에 덧붙인다 —
    리랭킹 대상이 아니며, 후보 생성에서 인용 불가 배경 맥락으로만 쓰인다.
    실패는 삼킨다(맥락 부재가 검색 실패가 되어선 안 된다).
    """
    queries = section_query_set(section, topic, analyst_catalog)
    query = queries[0]
    anchored = _rerank_query(section, topic)
    width = max(fetch_k, top_k) if reranker is not None else top_k
    if len(queries) == 1:
        hits = await client.search(query, project_id, track, width)
    else:
        # 질의별로 같은 폭을 받아 라운드로빈으로 걷어 width까지 채운다 — 리랭커가 보는
        # 후보 수는 그대로고(비용 불변), 그 안의 관점 다양성만 올라간다.
        results = await asyncio.gather(
            *(client.search(q, project_id, track, width) for q in queries),
            return_exceptions=True,
        )
        ranked = [r for r in results if isinstance(r, list)]
        failed = len(results) - len(ranked)
        if failed:
            # 보조 질의 하나가 죽었다고 절 검색을 통째로 버리지 않는다.
            logger.warning("retrieval.multi_query_partial", failed=failed, total=len(queries))
        hits = interleave_by_query(ranked, width) if ranked else []
        logger.info(
            "retrieval.multi_query",
            n_queries=len(queries),
            n_fused=len(hits),
            section=section.prompt_label(),
        )
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
    analyst_catalog: dict | None = None,
) -> SectionRetriever:
    """프로젝트·검색기에 바인딩된 SectionRetriever를 만든다 (write 루프 주입용).

    analyst_catalog는 배정된 에이전트의 검색 질의(AnalystSpec.queries)를 풀기 위한
    것이다 — 없으면 절 제목 질의 하나로 돌아간다.
    """

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
            analyst_catalog=analyst_catalog,
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
