"""섹션 부분 편집 — 한 섹션 재작성(regenerate_section)과 블록 국소 수정(rewrite_block).

재작성은 기존 write 파이프라인의 검색(SectionRetriever)+생성(generate_section_candidates)을
단일 섹션·후보 1개로 재사용한다. 사용자 지시(instruction)는 WriterContext.guidance에
얹어 프롬프트에 반영한다. 근거는 프로젝트 인덱스에서 실시간 검색한 chunk만 사용한다.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core import app_settings
from src.core.types import SectionDraft, SectionPlan
from src.services.generation.candidates import generate_section_candidates
from src.services.generation.effort import write_effort
from src.services.generation.split_writer import generate_section_split, plan_part_count
from src.services.generation.term_rules import format_term_injection
from src.services.generation.writer_context import build_writer_context, scale_for_evidence
from src.services.ledger import format_injection
from src.services.retrieval.section import SectionRetriever

logger = structlog.get_logger(__name__)


async def regenerate_section(
    *,
    section: SectionPlan,
    retrieve: SectionRetriever,
    instruction: str = "",
    client: LLMClient | None = None,
    model: str | None = None,
    plan_model: str | None = None,
    analyst_catalog: dict[str, Any] | None = None,
    rules: list[str] | None = None,
    term_entries: list[dict[str, Any]] | None = None,
    ledger_entries: list[dict[str, Any]] | None = None,
    ledger_chunks: list[Any] | None = None,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> SectionDraft:
    """한 섹션을 검색→생성으로 재작성해 SectionDraft 1개를 반환한다.

    작성 루프(workflows/write_loop)와 같은 조건으로 쓴다 — 배정된 페르소나·분량 목표를
    얹고, 목표가 단일 호출 한계를 넘으면 분할 생성한다. 조건이 다르면 재작성한 절만
    짧고 성격이 달라진다(2026-08-11: 재작성이 페르소나·분량을 통째로 잃고 있었다).

    instruction이 있으면 작성 방향에 최우선으로 얹는다. 인용·근거 제한 등 기본 작성
    규칙은 WriterContext가 그대로 유지한다.
    """
    ctx = build_writer_context(section, analyst_catalog, rules)
    search_plan = section
    if instruction.strip():
        extra = f"편집 지시(최우선 반영): {instruction.strip()}"
        guidance = f"{ctx.guidance}\n\n{extra}" if ctx.guidance else extra
        ctx = replace(ctx, guidance=guidance)
        # 지시문을 검색에도 반영한다 - 작성 프롬프트에만 넣으면 "일본 사례를 보강해줘"
        # 같은 절 계획 밖 지시에 맞는 청크가 근거 풀에 안 들어와, 모델이 지시를 못
        # 따르거나 근거 없이 쓴다(2026-08-14 사용자 결정). 주입 지점은 재채점 앵커
        # (direction → _rerank_query)뿐이다 - 1차 검색은 제목 키워드 AND 결합이라
        # 문장을 붙이면 재현율이 무너진다(section_search_query 주석). 생성 프롬프트는
        # 원본 section을 그대로 쓴다(지시문은 guidance로 이미 들어감).
        merged = " — ".join(p for p in (section.direction.strip(), instruction.strip()) if p)
        search_plan = section.model_copy(update={"direction": merged})

    chunks = await retrieve(search_plan)
    if ledger_chunks:
        # 사실 대장이 지목한 원 청크를 풀에 덧붙인다 - 작성 루프와 같은 조건
        # (write_loop._ledger_injection). 없으면 이어받은 값이 인용 불가로 나가고,
        # 재작성한 절만 builds_on 계약을 잃는다(2026-08-28 흐름 추적).
        have = {str(c.chunk_id) for c in chunks}
        chunks = [*chunks, *(c for c in ledger_chunks if str(c.chunk_id) not in have)]
    if ledger_entries:
        citable = [c for c in chunks if not c.is_summary]
        local_no = {str(c.chunk_id): i + 1 for i, c in enumerate(citable)}
        ledger_note = format_injection(ledger_entries, local_no)
        if ledger_note:
            joined = "\n\n".join(x for x in (ctx.guidance, ledger_note) if x)
            ctx = replace(ctx, guidance=joined)
    if term_entries:
        # 용어 규칙 주입 — 작성 루프와 같은 조건(write_loop 참조). 재작성만 빠지면
        # 그 절만 용어 표기·정의가 달라진다(페르소나를 잃던 병리와 같은 축).
        term_note, _ = format_term_injection(term_entries, chunks)
        if term_note:
            ctx = replace(ctx, guidance="\n\n".join(x for x in (ctx.guidance, term_note) if x))
    # 재료가 목표에 못 미치면 목표를 내린다 — 검색 뒤라야 실제 근거 수를 안다.
    base_min_chars = ctx.min_chars
    ctx = scale_for_evidence(ctx, sum(1 for c in chunks if not c.is_summary))
    volume_scaled = ctx.min_chars != base_min_chars
    write_model = model or app_settings.get_str("write_model")

    n_parts = plan_part_count(ctx.min_chars)
    if n_parts > 1:
        split = await generate_section_split(
            section,
            chunks,
            n_parts=n_parts,
            model=write_model,
            plan_model=plan_model,
            client=client,
            context=ctx,
            user_id=user_id,
            project_id=project_id,
        )
        if split is not None:
            return _scrubbed(split.model_copy(update={"volume_scaled": volume_scaled}))
        # 분할이 무너져 단일 호출로 간다 - 절이 짧아지고 인용이 준다. 흔적을 남긴다.
        fell_back = True
    else:
        fell_back = False

    drafts = await generate_section_candidates(
        section,
        chunks,
        n=1,
        model=write_model,
        client=client,
        context=ctx,
        user_id=user_id,
        project_id=project_id,
    )
    return _scrubbed(
        drafts[0].model_copy(update={"split_fallback": fell_back, "volume_scaled": volume_scaled})
    )


def _scrubbed(draft: SectionDraft) -> SectionDraft:
    """재작성 산출에도 조립과 같은 결정적 세정을 건다(2026-08-31).

    재작성 경로는 assemble을 안 지나므로 작성 잔재가 세정 없이 본문에 실렸다 —
    철강 4.3 재작성 실측: "(출처 56 대신 출처 106)" 교체 메모·"(출처 114 제외 …)"
    배정 메모가 그대로 노출. 세정은 의미를 안 바꾸는 결정적 치환뿐이라 안전하다.
    """
    from src.services.sections.scrub import scrub_leftovers

    cleaned, notes = scrub_leftovers(draft.content or "")
    if not notes:
        return draft
    logger.info("rewrite.scrubbed", section_id=str(draft.section_id), notes=notes)
    return draft.model_copy(update={"content": cleaned})


_BLOCK_REWRITE_PROMPT = """다음은 보고서의 한 절 전체입니다. \
이 가운데 [수정 대상 블록]만 지시에 따라 다시 작성하세요.

규칙:
- 수정 대상 블록의 대체 텍스트만 출력한다. 다른 문단·설명·머리말을 붙이지 않는다.
- 출처 번호는 절 본문에 이미 등장하는 번호만 쓸 수 있다.
  참고는 (출처 n), 원문을 그대로 옮긴 문장만 [n]이다.
  새 번호·새 수치·새 고유명사를 만들지 않는다.
- 마크다운 형식과 본문의 어조(개조식이면 개조식)를 유지한다.

[절 제목]
{title}

[절 전체 본문]
{content}

[수정 대상 블록]
{block}

[지시]
{instruction}"""

BLOCK_REWRITE_MAX_TOKENS = 2048


async def rewrite_block(
    *,
    section_title: str,
    content: str,
    block: str,
    instruction: str,
    client: LLMClient | None = None,
    model: str | None = None,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> str:
    """절 본문 중 지정 블록 하나만 재작성해 새 블록 텍스트를 반환한다.

    전체 재작성(regenerate_section)과 달리 검색을 다시 돌리지 않는다 — 근거는
    절이 이미 인용한 [n] 범위로 제한하고 새 수치 생성을 금지해 국소 수정에서의
    무근거 유입을 막는다. 본문 치환(splice)은 호출자 몫이다.
    """
    client = client or get_llm_client()
    prompt = _BLOCK_REWRITE_PROMPT.format(
        title=section_title,
        content=content,
        block=block,
        instruction=instruction.strip() or "문장을 더 명확하고 자연스럽게 다듬는다.",
    )
    rewrite_model = model or app_settings.get_str("write_model")
    request = CompletionRequest(
        messages=[Message(role="user", content=prompt)],
        model=rewrite_model,
        temperature=0.4,
        max_tokens=BLOCK_REWRITE_MAX_TOKENS,
        effort=write_effort(rewrite_model),
    )
    with token_context(user_id=user_id, project_id=project_id, operation="section_block_rewrite"):
        response = await client.complete(request)
    return response.content.strip()
