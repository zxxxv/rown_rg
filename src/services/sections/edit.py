"""섹션 부분 편집 — 한 섹션 재작성(regenerate_section)과 블록 국소 수정(rewrite_block).

재작성은 기존 write 파이프라인의 검색(SectionRetriever)+생성(generate_section_candidates)을
단일 섹션·후보 1개로 재사용한다. 사용자 지시(instruction)는 WriterContext.guidance에
얹어 프롬프트에 반영한다. 근거는 프로젝트 인덱스에서 실시간 검색한 chunk만 사용한다.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core import app_settings
from src.core.types import SectionDraft, SectionPlan
from src.services.generation.candidates import generate_section_candidates
from src.services.generation.split_writer import generate_section_split, plan_part_count
from src.services.generation.writer_context import build_writer_context, scale_for_evidence
from src.services.retrieval.section import SectionRetriever


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
    if instruction.strip():
        extra = f"편집 지시(최우선 반영): {instruction.strip()}"
        guidance = f"{ctx.guidance}\n\n{extra}" if ctx.guidance else extra
        ctx = replace(ctx, guidance=guidance)

    chunks = await retrieve(section)
    # 재료가 목표에 못 미치면 목표를 내린다 — 검색 뒤라야 실제 근거 수를 안다.
    ctx = scale_for_evidence(ctx, sum(1 for c in chunks if not c.is_summary))
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
            return split
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
    return drafts[0].model_copy(update={"split_fallback": fell_back})


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
    request = CompletionRequest(
        messages=[Message(role="user", content=prompt)],
        model=model or app_settings.get_str("write_model"),
        temperature=0.4,
        max_tokens=BLOCK_REWRITE_MAX_TOKENS,
    )
    with token_context(user_id=user_id, project_id=project_id, operation="section_block_rewrite"):
        response = await client.complete(request)
    return response.content.strip()
