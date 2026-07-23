"""섹션 부분 편집 — 한 섹션만 AI로 재작성.

기존 write 파이프라인의 검색(SectionRetriever)+생성(generate_section_candidates)을
단일 섹션·후보 1개로 재사용한다. 사용자 지시(instruction)는 WriterContext.guidance에
얹어 프롬프트에 반영한다. 근거는 프로젝트 인덱스에서 실시간 검색한 chunk만 사용한다.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from src.clients.llm.base import LLMClient
from src.core import app_settings
from src.core.types import SectionDraft, SectionPlan
from src.services.generation.candidates import generate_section_candidates
from src.services.generation.writer_context import build_writer_context
from src.services.retrieval.section import SectionRetriever


async def regenerate_section(
    *,
    section: SectionPlan,
    retrieve: SectionRetriever,
    instruction: str = "",
    client: LLMClient | None = None,
    model: str | None = None,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> SectionDraft:
    """한 섹션을 검색→생성으로 재작성해 SectionDraft 1개를 반환한다.

    instruction이 있으면 작성 방향에 최우선으로 얹는다. 인용([번호])·근거 제한 등
    기본 작성 규칙은 WriterContext가 그대로 유지한다.
    """
    ctx = build_writer_context(section)
    if instruction.strip():
        extra = f"편집 지시(최우선 반영): {instruction.strip()}"
        guidance = f"{ctx.guidance}\n\n{extra}" if ctx.guidance else extra
        ctx = replace(ctx, guidance=guidance)

    chunks = await retrieve(section)
    drafts = await generate_section_candidates(
        section,
        chunks,
        n=1,
        model=model or app_settings.get_str("write_model"),
        client=client,
        context=ctx,
        user_id=user_id,
        project_id=project_id,
    )
    return drafts[0]
