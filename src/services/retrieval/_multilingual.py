"""외국어 자료를 한글 질의로도 찾게 한다 — 번역 질의 병합.

**실측된 문제(2026-08-11).** 한글 절 제목으로 dense 검색을 돌리면 상위 20에 영문 청크가
**0건**이다. 같은 뜻의 영문 질의로 돌리면 6~12건이 올라온다. BGE-M3는 다국어 모델이지만
같은 언어끼리 훨씬 가깝게 붙는다. pgroonga(BM25)가 오히려 영문을 조금 건진다 - "dense는
교차언어가 되고 BM25는 안 된다"는 통념과 반대였다.

결과적으로 영문 자료를 아무리 많이 모아도 한글 보고서에는 거의 안 실린다. 수집 비용을
내고 색인까지 해 놓고 쓰지 않는 셈이다.

**해법은 질의 쪽을 고치는 것이다.** 청크를 번역해 이중 색인하면 저장·임베딩 비용이 두 배가
되고 원문 대조가 깨진다(모델이 받은 것과 사람이 보는 것이 달라진다). 질의를 번역해 한 번
더 검색하고 순위를 합치면 색인은 그대로 두고 같은 효과를 얻는다.

**외국어 자료가 없으면 번역도 하지 않는다.** 국내 자료만 있는 프로젝트가 대다수라 무조건
켜면 절마다 쓸모없는 LLM 콜이 하나씩 붙는다.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog
from sqlalchemy import text

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.services.retrieval.base import SearchHit

logger = structlog.get_logger(__name__)

QueryTranslator = Callable[[str, str], Awaitable[str]]

# 문맥 없이 절 제목만 주면 오역이 난다 - "시장 규모"는 market size지만, 주제가 반도체
# 장비면 'equipment market size'라야 영문 자료가 걸린다. 사전식 직역은 검색어로 약하다.
_SYSTEM = (
    "너는 검색 질의 번역기다. 한국어 보고서의 한 절을 검색하기 위한 영어 검색어를 만들어라.\n"
    "- 보고서 주제와 작성 방향을 읽고, 그 분야 영문 자료에서 실제로 쓰이는 용어를 골라라.\n"
    "- 직역하지 말고 검색어답게 만들어라(예: '예비타당성조사' → "
    "'preliminary feasibility study', '차세대 반도체 시장 규모' → "
    "'next-generation semiconductor market size forecast').\n"
    "- 기관·제도 고유명사는 널리 쓰이는 영문 표기를 쓴다.\n"
    "- 설명·따옴표 없이 영어 검색어만 한 줄로 출력하라(5~10단어)."
)
_MAX_TOKENS = 64
# 한글이 하나도 없는 청크를 외국어로 본다. 표·수치만 있는 청크가 섞이지만 그건 어느
# 언어로도 안 걸리는 청크라 판정을 왜곡하지 않는다.
_FOREIGN_SQL = (
    "SELECT count(*) FILTER (WHERE content !~ '[가-힣]'), count(*)"
    " FROM chunks WHERE project_id = :p AND coalesce(metadata->>'excluded', '') = ''"
)
_HANGUL_RE = re.compile(r"[가-힣]")


async def foreign_ratio(session_maker: Callable[[], object], project_id: UUID) -> float:
    """이 프로젝트 근거 풀에서 한글이 없는 청크의 비율(0~1). 실패하면 0."""
    try:
        async with session_maker() as session:  # type: ignore[attr-defined]
            row = (await session.execute(text(_FOREIGN_SQL), {"p": str(project_id)})).first()
    except Exception:
        logger.warning("multilingual.ratio_failed", project_id=str(project_id), exc_info=True)
        return 0.0
    if not row or not row[1]:
        return 0.0
    return (row[0] or 0) / row[1]


def make_query_translator(
    *,
    model: str,
    client: LLMClient | None = None,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> QueryTranslator:
    """(절 제목, 문맥) → 영문 검색어. 같은 입력은 프로세스 안에서 한 번만 번역한다.

    문맥에는 보고서 주제와 절의 작성 방향을 넣는다 — 제목만 주면 '시장 규모'가 무엇의
    시장인지 몰라 검색어로 쓸모없는 직역이 나온다.

    실패하면 빈 문자열을 돌려준다 — 부가 검색이 본 검색을 막아선 안 된다.
    """
    cache: dict[tuple[str, str], str] = {}

    async def translate(query: str, context: str = "") -> str:
        stripped = query.strip()
        if not stripped or not _HANGUL_RE.search(stripped):
            return ""  # 이미 영문이면 두 번 돌릴 이유가 없다
        key = (stripped, context.strip())
        if key in cache:
            return cache[key]
        prompt = f"[문맥] {context.strip()}\n[번역할 절 제목] {stripped}" if context else stripped
        try:
            with token_context(
                user_id=user_id, project_id=project_id, operation="retrieval.translate"
            ):
                response = await (client or get_llm_client()).complete(
                    CompletionRequest(
                        model=model,
                        system=_SYSTEM,
                        messages=[Message(role="user", content=prompt)],
                        max_tokens=_MAX_TOKENS,
                        cache_key=None,
                    )
                )
            translated = response.content.strip().strip('"').splitlines()[0].strip()
        except Exception:
            logger.warning("multilingual.translate_failed", query=stripped, exc_info=True)
            return ""
        cache[key] = translated
        return translated

    return translate


def make_gated_translator(
    session_maker: Callable[[], object],
    project_id: UUID,
    *,
    model: str,
    min_foreign_ratio: float,
    user_id: UUID | None = None,
    client: LLMClient | None = None,
) -> QueryTranslator:
    """외국어 자료가 있는 프로젝트에서만 번역하는 번역기.

    판정은 첫 호출 때 한 번만 하고 그 결과를 계속 쓴다 - 검색기를 만드는 곳이 동기
    팩토리라 그 시점엔 DB를 볼 수 없고, 절마다 세는 것도 낭비다. 색인은 작성 시작
    전에 끝나 있어 런 도중 비율이 바뀌지 않는다.
    """
    inner = make_query_translator(
        model=model, client=client, user_id=user_id, project_id=project_id
    )
    decided: dict[str, bool] = {}

    async def translate(query: str, context: str = "") -> str:
        if "on" not in decided:
            ratio = await foreign_ratio(session_maker, project_id)
            decided["on"] = ratio >= min_foreign_ratio
            logger.info(
                "retrieval.multilingual_gate",
                project_id=str(project_id),
                foreign_ratio=round(ratio, 3),
                enabled=decided["on"],
            )
        if not decided["on"]:
            return ""
        return await inner(query, context)

    return translate


def merge_rankings(primary: list[SearchHit], secondary: list[SearchHit], *, k: int = 60) -> list:
    """두 순위를 RRF로 합친다. 같은 청크는 먼저 본 hit을 남긴다.

    본 질의를 앞에 두는 이유: 한글 질의가 원본이고 번역은 보조다. 동점이면 원본 순위가
    이긴다(정렬이 안정 정렬이라 순서가 보존된다).
    """
    scores: dict[UUID, float] = {}
    first: dict[UUID, SearchHit] = {}
    for ranking in (primary, secondary):
        for rank, hit in enumerate(ranking):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            first.setdefault(hit.chunk_id, hit)
    ordered = sorted(scores, key=lambda cid: -scores[cid])
    return [first[cid] for cid in ordered]
