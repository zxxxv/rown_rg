"""섹션 부분 편집 — 한 섹션 재작성(regenerate_section)과 블록 국소 수정(rewrite_block).

재작성은 기존 write 파이프라인의 검색(SectionRetriever)+생성(generate_section_candidates)을
단일 섹션·후보 1개로 재사용한다. 사용자 지시(instruction)는 WriterContext.guidance에
얹어 프롬프트에 반영한다. 근거는 프로젝트 인덱스에서 실시간 검색한 chunk만 사용한다.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
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


_BLOCK_REWRITE_PROMPT = """다음은 보고서의 한 절 전체입니다. \
이 가운데 [수정 대상 블록]만 지시에 따라 다시 작성하세요.

규칙:
- 수정 대상 블록의 대체 텍스트만 출력한다. 다른 문단·설명·머리말을 붙이지 않는다.
- 인용 번호는 절 본문에 이미 등장하는 [n]만 쓸 수 있다. 새 번호·새 수치·새 고유명사를 만들지 않는다.
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
