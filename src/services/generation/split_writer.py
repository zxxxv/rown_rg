"""분할 생성 — volume_target이 단일 호출 출력 한계를 넘는 절을 파트로 나눠 결합.

단일 LLM 호출은 재료·max_tokens·프롬프트와 무관하게 4~8천자에서 스스로 멈춘다
(2026-08-07 top_k 곡선 실측 — 재료 2.2배에 분량 -17%). 목표 분량이 그 위인 절은
호출을 나누는 것 외의 경로가 없다. 프로토타입(exp_split2) 실증: 분량 7배, 무근거
밀도 flat 이하, 파트 간 침범은 '근거 배타 배정'이 억제.

설계 불변식:
- 검색은 절당 1회(top_k 유지) — 파트는 같은 근거 풀을 같은 번호로 공유한다.
  결합 후 [n] 인용 정합이 자동 유지되고, 프롬프트 프리픽스가 파트 간 동일해져
  프롬프트 캐싱(cache_prefix_messages)이 2번째 파트부터 입력을 0.1배로 읽는다.
- 파트별 인용은 배타 배정된 부분집합만 허용(지시) — 전 파트가 같은 강한 청크로
  수렴해 서로를 재서술하는 v1 실패 모드 차단. 위반은 로그로만 남긴다([n] 자체는
  전체 풀 기준 유효 인용이라 무결성 문제가 아님 — 중복 억제 장치일 뿐).
- 이어쓰기 컨텍스트는 부정 목록(소제목·수치)만 — 앞 파트 본문을 프롬프트에 넣으면
  전사 오염(계층 실험 +39% 실증)과 캐시 프리픽스 파괴가 같이 온다.
- 실패는 비치명 — 파트 계획·배정이 무너지면 None을 돌려 단일 호출 경로로 폴백.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message, incomplete_stop
from src.clients.llm.token_tracker import token_context
from src.core.citations import numbers_in_order
from src.core.config import settings
from src.core.types import RetrievedChunk, SectionDraft, SectionPlan
from src.services.generation.candidates import _build_prompt, _extract_cited_ids
from src.services.generation.writer_context import WriterContext

if TYPE_CHECKING:
    from src.clients.embedding_client import EmbeddingClient

logger = structlog.get_logger(__name__)

PART_MAX_TOKENS = 10_000  # 파트 출력 상한 — 실측 파트 산출 2~3.3천자(3~5천 토큰)의 헤드룸
# 계획 출력 상한 — 600은 Sonnet 기준이었다. Opus는 소제목을 1.7배 길게 써서 6개를
# 넘기다 상한에 잘렸고, 잘린 JSON은 파서가 통째로 버려 분할이 무너졌다(2026-08-11 실측:
# 출력 정확히 600 = 상한). 계획은 절당 1콜이라 상한을 올려도 비용이 무의미하다.
PLAN_MAX_TOKENS = 1_200
MIN_CHUNKS_PER_PART = 3  # 배정 근거가 이보다 적은 파트는 병합(빈약 파트가 침범 유혹의 원천)
_NUM_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

_PLAN_SYSTEM = (
    "너는 보고서 절 설계자다. 주어진 절 제목·작성 방향·핵심 포인트·근거 미리보기로 "
    "이 절을 구성할 소주제 {n}개를 정하라. 소주제끼리 내용이 겹치지 않게 상호 배타적으로 "
    "나누고, 근거 자료로 실제 채울 수 있는 주제만 골라라. 설명 없이 JSON 배열만 출력:\n"
    '["소주제1", "소주제2", ...]'
)


# 파트 하나가 한 번의 호출로 낼 수 있는 현실 상한(실측 4~8천자) — 이보다 큰 목표를
# 파트에 주면 지시만 크고 결과는 안 늘어난다.
_MAX_PART_GOAL_CHARS = 4000


def plan_part_count(min_chars: int | None) -> int:
    """volume_target min → 파트 수. 분할이 꺼져 있거나 목표가 작으면 1(단일 호출)."""
    if not settings.write_split_enabled or not min_chars:
        return 1
    per_part = max(1, settings.write_split_chars_per_part)
    return max(1, min(settings.write_split_max_parts, -(-min_chars // per_part)))


def assign_by_similarity(
    chunk_vecs: Sequence[Sequence[float]],
    part_vecs: Sequence[Sequence[float]],
    *,
    min_per_part: int = MIN_CHUNKS_PER_PART,
) -> tuple[list[list[int]], list[int]]:
    """청크→파트 배타 배정(결정적 greedy) + 빈약 파트 병합.

    전역 (청크, 파트) 유사도 내림차순으로 청크를 한 번씩 배정하고 파트당 캡을 둔다.
    배정 수가 min_per_part 미만인 파트는 제거 후 재배정(병합) — 재료가 없는 파트는
    짧게 나오고 다른 파트를 침범한다(프로토타입 3.2 실측). 반환은
    (파트별 근거 번호(1-base) 목록, 살아남은 파트의 원래 인덱스) — 중간 파트가
    제거될 수 있어 호출부는 인덱스로 제목을 재정렬해야 한다.
    파트가 1개로 줄면 호출부가 단일 호출로 폴백한다.
    """
    import numpy as np

    cvecs = np.asarray(chunk_vecs, dtype=np.float32)
    active = list(range(len(part_vecs)))
    while active:
        pvecs = np.asarray([part_vecs[p] for p in active], dtype=np.float32)
        sims = cvecs @ pvecs.T  # (n_chunks, n_active)
        cap = max(1, -(-len(chunk_vecs) // len(active)) + 1)
        order = sorted(
            (
                (float(sims[ci, ai]), ci, ai)
                for ci in range(len(chunk_vecs))
                for ai in range(len(active))
            ),
            key=lambda t: (-t[0], t[1], t[2]),
        )
        assigned: dict[int, int] = {}
        counts = [0] * len(active)
        for _, ci, ai in order:
            if ci in assigned or counts[ai] >= cap:
                continue
            assigned[ci] = ai
            counts[ai] += 1
        if len(active) > 1 and min(counts) < min_per_part:
            # 가장 빈약한 파트를 제거하고 전체 재배정 — 결정적(최소 배정, 동률이면 앞 파트)
            drop = counts.index(min(counts))
            active.pop(drop)
            continue
        groups_by_part: dict[int, list[int]] = {p: [] for p in active}
        for ci, ai in sorted(assigned.items()):
            groups_by_part[active[ai]].append(ci + 1)
        return [groups_by_part[p] for p in active], list(active)
    return [], []


def _headers(text_block: str, limit: int = 20) -> list[str]:
    """파트 산출물의 □/ㅇ/마크다운 소제목만 추출 — 부정 목록용(본문 전사 없음)."""
    out: list[str] = []
    for raw in text_block.splitlines():
        s = raw.strip()
        if s.startswith(("□", "ㅇ", "#")):
            t = s.lstrip("□ㅇ# ").strip()[:40]
            if t and t not in out:
                out.append(t)
    return out[:limit]


def _numbers_used(texts: Sequence[str], limit: int = 40) -> list[str]:
    """앞 파트들의 수치 토큰(유니크·등장순) — 재서술 금지 부정 목록용."""
    seen: list[str] = []
    for t in texts:
        for m in _NUM_TOKEN_RE.finditer(t):
            tok = m.group()
            digits = tok.replace(",", "").replace(".", "").replace("%", "")
            if len(digits) < 2 or (len(digits) == 4 and digits.startswith(("19", "20"))):
                continue  # 한 자리 수·연도는 잡음
            if tok not in seen:
                seen.append(tok)
    return seen[:limit]


def _part_tail(
    idx: int,
    n_parts: int,
    title: str,
    all_titles: Sequence[str],
    allowed: Sequence[int],
    prev_headers: Sequence[str],
    used_numbers: Sequence[str],
    per_part_goal: int | None = None,
) -> str:
    """파트별 가변 지시 — 공유 프리픽스 뒤에 별도 메시지로 붙는다(캐시 경계 밖)."""
    others = ", ".join(f"'{t}'" for t in all_titles if t != title)
    allowed_s = ", ".join(str(n) for n in allowed)
    # 파트당 목표는 절 목표를 실제 파트 수로 나눈 값이다. 고정값을 쓰면 파트 수가
    # 요청보다 적게 계획됐을 때 그 곱이 곧 천장이 된다 — 실측(2026-08-10): 목표
    # 20,000자 절이 6파트로 계획되자 6×2,250=13,500자에서 정확히 멈췄다.
    per_part_goal = per_part_goal or settings.write_split_chars_per_part
    lines = [
        "[파트 작성 지시]",
        f"이 절은 파트 {n_parts}개로 나눠 작성 중이고, 너는 파트 {idx}/{n_parts}"
        f" — 소주제 '{title}'만 {per_part_goal:,}자 안팎으로 집중 작성하라.",
        f"절 전체의 구조나 요약을 만들지 마라. 다른 소주제({others})는 다른 파트가 쓴다"
        " — 그 영역을 침범하거나 미리 요약하지 마라.",
        f"이 파트에서 쓸 수 있는 근거는 {allowed_s}번 뿐이다. 목록에 없는 번호는 쓰지 마라."
        " 출처는 문장 끝에 (출처 n)으로 달고, 여러 근거를 함께 달 때는 (출처 n, m)처럼"
        " 한 괄호에 오름차순으로 모아라.",
        "형식은 절 본문과 동일한 개조식(□ 대주제 → ㅇ 소주제 → - 세부)을 유지하라.",
        # 아래 4줄은 산출물 실독(2026-08-08) 결함 교정 — 지표(형식·무근거)는 통과하는데
        # 심사자가 먼저 지적할 항목들이다.
        "자료가 '무엇을 조사·측정했는지'(연구 방법·조사 항목·표본 구성·분석 기법)를 서술하지 마라."
        " 그 자료가 밝혀낸 결과·수치·경향만 쓴다. 결과가 근거에 없으면 그 문장은 빼라.",
        "비교 수치(오즈비·배수·증감률)는 무엇 대비인지 기준을 함께 밝혀라. 기준이 근거에"
        " 없으면 그 수치를 쓰지 마라.",
        "한 '-' 항목은 한 가지 주장만 2줄 이내로 쓴다. 여러 절을 접속사로 잇지 마라.",
        "직전 항목을 바꿔 말하는 마무리('따라서 ~을 시사함/의미함')를 덧붙이지 마라"
        " — 새로운 정보가 없으면 쓰지 않는다.",
        "'제공 근거에는/자료에 제시되어 있음'처럼 근거 목록이나 작성 상황을 언급하지 마라."
        " 독자는 근거 꾸러미를 보지 못한다 — 확인된 사실만 단정해서 쓴다.",
        "전문용어·약어는 첫 등장에 한글(영문 원어, 약어) 형식으로 병기하라.",
        "분석 결과는 가능하면 이 파트에 표 1개로 정리하라(마크다운 표, 헤더 포함)."
        " 표 바로 윗줄에 '표: 제목'을 쓰고 바로 아래 줄에 '* 출처: (출처 n, m)'을 붙여라."
        " 표 앞에 'table' 같은 표식은 쓰지 마라.",
    ]
    if idx == 1:
        lines.append("절 제목 헤딩과 2~3문장 도입은 이 파트에만 포함하라.")
    else:
        lines.append("절 제목 헤딩·도입·전환 문단 없이 곧바로 '□' 대주제 블록으로 시작하라.")
    if idx < n_parts:
        lines.append("종합·마무리·결론 문단을 쓰지 마라(마지막 파트의 몫이다).")
    else:
        lines.append("마지막 파트이니 절 전체를 닫는 마무리는 2~3문장까지만 허용한다.")
    if prev_headers:
        lines.append(
            "앞 파트에서 이미 다룬 소제목(같은 주제 반복 서술 금지): " + " / ".join(prev_headers)
        )
    if used_numbers:
        lines.append("앞 파트에서 이미 사용된 수치(재서술 금지): " + ", ".join(used_numbers))
    return "\n".join(lines)


def _cite_violations(part_text: str, allowed: Sequence[int], n_citable: int) -> int:
    """허용 목록 밖 인용 수 — 배타 배정 준수 진단(로그 전용, 게이트 아님)."""
    allowed_set = set(allowed)
    return sum(1 for n in numbers_in_order(part_text) if n <= n_citable and n not in allowed_set)


async def _plan_subtopics(
    client: LLMClient,
    section: SectionPlan,
    citable: Sequence[RetrievedChunk],
    *,
    n_parts: int,
    model: str,
) -> list[str]:
    """파트 소주제 계획 1콜 — 제목 수준 산출이라 전사 오염 리스크가 낮다."""
    previews = "\n".join(f"- {c.content[:80]}" for c in citable[:8])
    key_points = ", ".join(section.key_points) or "(없음)"
    prompt = (
        f"절 제목: {section.chapter_number}.{section.section_number} {section.title}\n"
        f"작성 방향: {section.direction or '(없음)'}\n"
        f"핵심 포인트: {key_points}\n\n근거 미리보기:\n{previews}"
    )
    request = CompletionRequest(
        messages=[Message(role="user", content=prompt)],
        model=model,
        system=_PLAN_SYSTEM.format(n=n_parts),
        temperature=0.0,
        max_tokens=PLAN_MAX_TOKENS,
    )
    response = await client.complete(request)
    return parse_plan_titles(response.content, n_parts)


# 잘린 JSON에서도 완성된 항목은 건진다 — 닫는 대괄호를 못 찾으면 통째로 버리던 파서가
# 상한에 걸린 응답을 0개로 만들었고, 절 하나가 단일 호출로 떨어졌다. 상한을 올려도
# 언젠가 또 걸리므로 파서 쪽이 근본이다.
_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def parse_plan_titles(content: str, n_parts: int) -> list[str]:
    """계획 응답 → 소주제 목록. JSON이 온전하면 그대로, 잘렸으면 완성된 문자열만."""
    m = re.search(r"\[.*\]", content, re.DOTALL)
    if m is not None:
        try:
            parsed = json.loads(m.group())
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [p.strip() for p in parsed if isinstance(p, str) and p.strip()][:n_parts]
    start = content.find("[")
    if start < 0:
        return []
    # 마지막 항목은 따옴표가 안 닫혀 잘렸을 수 있다 — 닫힌 것만 취한다.
    titles = [t.strip() for t in _QUOTED_RE.findall(content[start:]) if t.strip()]
    return titles[:n_parts]


async def _load_chunk_vectors(
    citable: Sequence[RetrievedChunk],
) -> list[list[float]] | None:
    """청크 임베딩을 DB에서 로드(색인 계약: chunks.embedding 보유). 결손 시 None."""
    from sqlalchemy import text

    from src.db.session import async_session_maker

    ids = [str(c.chunk_id) for c in citable]
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                text("select id, embedding from chunks where id = any(:ids)"),
                {"ids": ids},
            )
        ).all()
    by_id: dict[str, list[float]] = {}
    for r in rows:
        emb = r.embedding
        if isinstance(emb, str):  # asyncpg가 vector를 텍스트로 줄 수 있다
            emb = [float(x) for x in emb.strip("[]").split(",")]
        by_id[str(r.id)] = list(emb)
    vectors = [by_id.get(i) for i in ids]
    if any(v is None for v in vectors):
        return None
    return vectors  # type: ignore[return-value]


async def generate_section_split(
    section: SectionPlan,
    chunks: Sequence[RetrievedChunk],
    *,
    n_parts: int,
    context: WriterContext,
    model: str,
    plan_model: str | None = None,
    client: LLMClient | None = None,
    base_temperature: float = 0.7,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
    embedder: EmbeddingClient | None = None,
) -> SectionDraft | None:
    """절 1개를 파트로 분할 생성해 결합한 SectionDraft를 돌려준다. 실패 시 None(폴백 신호).

    파트 호출은 순차다 — 부정 목록이 앞 파트에 의존하고, 프롬프트 캐시도 첫 응답
    이후에야 읽을 수 있다(병렬이면 캐시 미적중). 결합본의 [n]은 전체 근거 풀 번호
    그대로라 기존 게이트·인용 전역 번호화 경로가 무수정으로 동작한다.

    plan_model: 파트 소주제 계획(절당 1콜·600토큰)만 다른 모델로 돌린다. 문서 구조는
    이 계획이 좌우하는 반면 비용은 무시할 수준이라, 본문은 저가 모델로 쓰면서 구조만
    상위 모델로 잡는 조합이 가능하다. None이면 model과 동일.
    """
    citable = [c for c in chunks if not c.is_summary]
    if len(citable) < MIN_CHUNKS_PER_PART * 2:
        return None  # 풀이 두 파트도 못 채우면 분할 의미 없음
    if client is None:
        from src.clients.llm.factory import get_llm_client

        client = get_llm_client()

    operation = f"section_write:{section.chapter_number}.{section.section_number}"
    with token_context(user_id=user_id, project_id=project_id, operation=operation):
        part_titles = await _plan_subtopics(
            client, section, citable, n_parts=n_parts, model=plan_model or model
        )
    if len(part_titles) < 2:
        logger.warning(
            "split_writer.plan_failed",
            section=f"{section.chapter_number}.{section.section_number}",
            n_titles=len(part_titles),
        )
        return None

    if embedder is None:
        from src.clients.embedding_factory import get_embedding_client

        embedder = get_embedding_client()
    chunk_vecs = await _load_chunk_vectors(citable)
    if chunk_vecs is None:
        logger.warning("split_writer.chunk_vectors_missing", section=section.title)
        return None
    part_vecs = [(await embedder.embed(t)).embedding for t in part_titles]
    groups, surviving = assign_by_similarity(chunk_vecs, part_vecs)
    if len(groups) < 2:
        logger.info(
            "split_writer.merged_to_single",
            section=f"{section.chapter_number}.{section.section_number}",
        )
        return None
    # 병합으로 중간 파트가 제거될 수 있다 — 생존 인덱스로 제목을 재정렬(어긋나면
    # 파트가 남의 근거 목록을 받아 배타 배정이 무의미해진다)
    part_titles = [part_titles[p] for p in surviving]

    prefix = _build_prompt(section, chunks, guidance=context.guidance)
    parts: list[str] = []
    # 계획이 요청보다 적은 파트를 돌려주면(자주 있다) 파트당 목표를 그만큼 올려
    # 절 목표를 지킨다. 상한은 단일 호출 출력 한계(실측 4~8천자) 근처로 묶는다.
    goal_per_part = settings.write_split_chars_per_part
    if context.min_chars:
        goal_per_part = max(
            goal_per_part, min(_MAX_PART_GOAL_CHARS, -(-context.min_chars // len(part_titles)))
        )
    headers: list[str] = []
    violations = 0
    with token_context(user_id=user_id, project_id=project_id, operation=operation):
        for i, title in enumerate(part_titles, start=1):
            tail = _part_tail(
                i,
                len(part_titles),
                title,
                part_titles,
                groups[i - 1],
                headers,
                _numbers_used(parts),
                per_part_goal=goal_per_part,
            )
            request = CompletionRequest(
                messages=[
                    Message(role="user", content=prefix),
                    Message(role="user", content=tail),
                ],
                model=model,
                system=context.system,
                temperature=base_temperature,
                max_tokens=PART_MAX_TOKENS,
                cache_prefix_messages=1,  # 프리픽스 공유 — 파트 2+부터 캐시 읽기
            )
            response = await client.complete(request)
            # 파트 미완결(max_tokens 컷·refusal 등)은 결합본 중간에 문장 토막을 심는다
            # — 같은 호출 1회 재시도, 그래도 미완결이면 분할 포기(None) → 단일 호출
            # 폴백. 폴백 초안의 미완결은 후보 게이트(check_complete)가 다시 본다.
            reason = incomplete_stop(response.stop_reason)
            if reason:
                logger.warning(
                    "split_writer.part_incomplete",
                    section=f"{section.chapter_number}.{section.section_number}",
                    part=i,
                    stop_reason=reason,
                )
                response = await client.complete(request)
                reason = incomplete_stop(response.stop_reason)
                if reason:
                    logger.warning(
                        "split_writer.part_incomplete_giving_up",
                        section=f"{section.chapter_number}.{section.section_number}",
                        part=i,
                        stop_reason=reason,
                    )
                    return None
            part = response.content.strip()
            parts.append(part)
            headers += _headers(part)
            violations += _cite_violations(part, groups[i - 1], len(citable))

    combined = "\n\n".join(parts)
    logger.info(
        "split_writer.section_done",
        section=f"{section.chapter_number}.{section.section_number}",
        n_parts=len(part_titles),
        part_chars=[len(p) for p in parts],
        total_chars=len(combined),
        cite_violations=violations,
    )
    return SectionDraft(
        section_id=section.section_id,
        content=combined,
        cited_chunk_ids=_extract_cited_ids(combined, citable),
        pool_chunk_ids=[c.chunk_id for c in citable],
    )
