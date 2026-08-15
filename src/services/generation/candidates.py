"""섹션 후보 생성 — LLM router로 N개 초안을 뽑는다 (판정 아님, 생성만).

무한성 캡: N은 상수. Writer↔Critic 루프가 아니라 1회 fan-out이다.
출처는 UUID를 모델에 노출하지 않고 번호 마커로 받아 인덱스→chunk_id로 매핑한다
(LLM이 긴 UUID를 정확히 복제하지 못하는 문제 회피 + hallucinated 인용 구조적 차단).
표기 문법은 src/core/citations.py 참고 — (출처 n)=참고, [n]=직접 인용.
합격/불합격 판정은 여기서 하지 않는다 — gate.py의 정적 검사 몫이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from src.clients.llm.base import CompletionRequest, LLMClient, Message, incomplete_stop
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.citations import numbers_in_order
from src.core.types import RetrievedChunk, SectionDraft, SectionPlan
from src.services.generation.effort import write_effort
from src.services.generation.writer_context import WriterContext, build_writer_context

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_N = 2
TEMPERATURE_STEP = 0.15  # 후보 간 다양성용 temperature 증분
TEMPERATURE_CEILING = 1.0


def _source_label(chunk: RetrievedChunk) -> str:
    """근거 앞에 붙는 '무엇의 어디' 표기 - 없으면 빈 문자열(형식 불변).

    발간연도를 함께 실어 옛 자료의 통계가 무연도 현재형으로 서술되는 것을 막는다
    (검증런 2회 연속의 주 감점 축, 2026-08-15). 미상이면 안 붙인다 - 전 청크에
    '미상'을 달면 라벨이 소음이 된다.
    """
    year = f"{chunk.published_year}년 발간" if chunk.published_year else ""
    parts = [p for p in [chunk.source_title, year, " > ".join(chunk.header_path)] if p]
    return f" ({' · '.join(parts)})" if parts else ""


def _build_prompt(
    section: SectionPlan, chunks: Sequence[RetrievedChunk], guidance: str = ""
) -> str:
    """후보 생성 유저 프롬프트 — [번호] 인용 풀은 leaf 청크만.

    RAPTOR 요약(is_summary=True)은 번호 없는 '배경 맥락' 블록으로 분리한다 —
    모델이 흐름 파악에는 쓰되 인용하지 못하게(참고문헌 무결성).
    """
    citable = [c for c in chunks if not c.is_summary]
    summaries = [c for c in chunks if c.is_summary]
    lines = [f"작성할 섹션: {section.prompt_label()}"]
    if section.chapter_title and section.chapter_title.lower() not in section.title.lower():
        # 같은 절 제목이 장마다 반복되는 비교형 목차에서 범위를 못 박는다 — 이 문장이
        # 없어서 2·3·4장의 같은 번호 절이 서로 구별되지 않는 글로 나왔다(2026-08-14).
        lines.append(
            f"이 절은 '{section.chapter_title}' 장에 속한다 — 그 대상에 한정해 쓰고, "
            "다른 장이 맡은 대상은 비교가 꼭 필요할 때만 한 줄로 언급하라."
        )
    lines.append("")
    if guidance:
        # 플래너 산출물(작성 방향·핵심 포인트) — 프리셋 경로에서만 채워진다.
        lines.extend([guidance, ""])
    if summaries:
        # 배경 요약 인용 규칙(2026-08-05): Haiku가 '[배경자료 제공됨]'류 마커를 발명해
        # 문서를 오염시킨 실사례 재발 방지 — 마커 금지·수치 사용 금지를 명시한다.
        lines.append(
            "배경 맥락 (전체 자료의 AI 요약 — 논지 파악용 참고 전용):"
            " 이 블록은 출처로 쓸 수 없다. [배경자료], [배경 맥락] 같은 어떤 형태의"
            " 대괄호 표기도 만들지 마라. 이 블록의 수치·통계는 본문 근거로 쓰지 말고,"
            " 수치가 필요하면 아래 번호 있는 근거 자료에서 찾아 (출처 n)으로 표기하라."
        )
        lines.extend(f"- {s.content}" for s in summaries)
        lines.append("")
    lines.append("근거 자료:")
    for i, ch in enumerate(citable, start=1):
        lines.append(f"[{i}]{_source_label(ch)} {ch.content}")
    lines.extend(
        [
            "",
            "위 근거만으로 해당 섹션 본문을 작성하라. 각 주장 끝에 근거 번호를 (출처 n)으로"
            " 표기하라. 근거를 여럿 썼으면 (출처 n, m)처럼 한 괄호에 모아라."
            " 원문 문장을 따옴표로 감싸 그대로 옮긴 경우에만 문장 끝에 [n]을 쓴다 —"
            " 참고해 다시 쓴 문장에는 절대 대괄호를 쓰지 마라.",
            # 자료 시점 규칙(2026-08-15) — '21~'22 자료의 수치가 무연도 현재형으로
            # 유통되던 결함('전기요금 40% 저렴' 등)의 예방. 라벨 연도가 재료다.
            "근거 라벨의 '○○년 발간'을 보라: 발간이 오래된 자료(2년 이상 경과)의 실적·"
            "통계·시장 여건을 쓸 때는 그 시점을 문장에 명기하고('○○년 기준', '○○년"
            " 발간 자료 기준'), 현재형으로 단정하지 마라. 발간연도가 없는 근거의 통계도"
            " 시점을 현재로 단정하지 마라. 제도 조문·정의처럼 시점과 무관한 서술은 예외다.",
        ]
    )
    return "\n".join(lines)


def _extract_cited_ids(content: str, chunks: Sequence[RetrievedChunk]) -> list[UUID]:
    """본문 마커의 번호를 인덱스로 해석해 chunk_id로 매핑. 범위 밖 마커는 무시.

    참고 표기 (출처 n)과 직접 인용 [n]을 가리지 않는다 — 둘 다 "몇 번 근거를 썼는가"다.
    등장 순서를 보존하며 중복 제거한다. 범위 밖 번호는 버리므로 여기서 나온
    cited_chunk_ids는 항상 chunks 풀 안에 있다(gate의 citation_resolves는 수동
    편집·다른 생성 경로를 위한 방어선으로 남는다).
    """
    ids: list[UUID] = []
    seen: set[UUID] = set()
    for n in numbers_in_order(content):
        idx = n - 1
        if 0 <= idx < len(chunks):
            cid = chunks[idx].chunk_id
            if cid not in seen:
                seen.add(cid)
                ids.append(cid)
    return ids


async def _one_candidate(
    client: LLMClient,
    section: SectionPlan,
    chunks: Sequence[RetrievedChunk],
    prompt: str,
    *,
    system: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> SectionDraft:
    request = CompletionRequest(
        messages=[Message(role="user", content=prompt)],
        model=model,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        effort=write_effort(model),
    )
    response = await client.complete(request)
    return SectionDraft(
        section_id=section.section_id,
        content=response.content,
        cited_chunk_ids=_extract_cited_ids(response.content, chunks),
        # 인용 가능한 것만 — 요약 노드는 [n] 풀에서 빠지므로 여기서도 뺀다.
        pool_chunk_ids=[c.chunk_id for c in chunks if not c.is_summary],
        # max_tokens 컷·refusal 등 미완결 종료를 초안에 새긴다 — 게이트가 HARD 제외해
        # 문장 중간에 끊긴 토막이 '완성 절'로 채택되는 것을 막는다(2026-08-13 실사고).
        incomplete_reason=incomplete_stop(response.stop_reason),
    )


async def generate_section_candidates(
    section: SectionPlan,
    chunks: Sequence[RetrievedChunk],
    *,
    n: int = DEFAULT_N,
    model: str = DEFAULT_MODEL,
    client: LLMClient | None = None,
    base_temperature: float = 0.7,
    max_tokens: int | None = None,
    context: WriterContext | None = None,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> list[SectionDraft]:
    """한 섹션에 대해 후보 초안 N개를 생성한다. N은 상수(무한성 캡).

    프롬프트·분량은 WriterContext가 결정한다(미지정 시 섹션에서 조립) —
    프리셋 경로면 분석 에이전트 페르소나·작성 방향·핵심 포인트·volume_target이
    반영된다. 후보마다 temperature를 조금씩 올려 다양성을 확보하고, 토큰
    사용량은 token_context로 (user_id, project_id, operation)에 귀속된다.
    """
    if n < 1:
        raise ValueError("n은 1 이상이어야 합니다")
    client = client or get_llm_client()
    ctx = context or build_writer_context(section)
    prompt = _build_prompt(section, chunks, guidance=ctx.guidance)
    # [n] 마커는 인용 가능 풀(leaf)의 인덱스다 — 프롬프트 번호 매김과 동일한 리스트.
    citable = [c for c in chunks if not c.is_summary]
    resolved_max_tokens = max_tokens if max_tokens is not None else ctx.max_tokens
    temps = [min(base_temperature + i * TEMPERATURE_STEP, TEMPERATURE_CEILING) for i in range(n)]
    with token_context(
        user_id=user_id,
        project_id=project_id,
        operation=f"section_write:{section.chapter_number}.{section.section_number}",
    ):
        drafts = await asyncio.gather(
            *(
                _one_candidate(
                    client,
                    section,
                    citable,
                    prompt,
                    system=ctx.system,
                    model=model,
                    temperature=t,
                    max_tokens=resolved_max_tokens,
                )
                for t in temps
            )
        )
    return list(drafts)
