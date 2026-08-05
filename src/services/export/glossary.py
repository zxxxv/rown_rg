"""약어 사전 빌더 — 조립 시 1콜로 약어 설명을 생성해 projects.glossary에 저장.

약어 정리표의 "설명" 열은 결정적 추출(풀네임)만으로는 빈약하다(2026-08-05 지적).
선택 확정 본문에서 "풀네임(약어)" 표기를 결정적으로 뽑고, 설명 문장만 LLM 1콜로
채운다(최저가 모델). 실패는 비치명 — 설명 없이 풀네임만으로 렌더한다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.config import settings
from src.core.state import ProjectState
from src.services.generation.planner import _parse_manifest

logger = structlog.get_logger(__name__)

_MAX_ABBRS = 40  # 프롬프트·표 크기 캡 — 문서에 이 이상이면 첫 등장 순 상위만
DEFAULT_MAX_TOKENS = 2000

_SYSTEM = (
    "너는 보고서 편집자다. 약어 목록을 받아 각 약어가 이 보고서 맥락에서 무엇을"
    " 뜻하는지 한 줄(40자 이내) 한국어 설명을 쓴다. 사실만 간결하게 쓰고, 모르는"
    " 약어는 빈 문자열로 둔다. 마지막에 JSON만 출력한다:"
    ' ```json\n{"glossary": {"약어": "설명", ...}}\n```'
)


def extract_abbreviations(state: ProjectState) -> dict[str, str]:
    """선택 확정 본문 전체에서 약어→풀네임을 첫 등장 순서로 추출(결정적)."""
    from src.services.export.report import _collect_abbreviations, _strip_citations

    acc: dict[str, str] = {}
    for draft in state.selected_drafts():
        _collect_abbreviations(_strip_citations(draft.content), acc)
        if len(acc) >= _MAX_ABBRS:
            break
    return dict(list(acc.items())[:_MAX_ABBRS])


async def build_glossary(
    state: ProjectState,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> dict[str, dict[str, str]] | None:
    """{약어: {full, desc}} 사전 생성 — 약어가 없으면 None."""
    abbrs = extract_abbreviations(state)
    if not abbrs:
        return None
    client = client or get_llm_client()
    model = model or settings.glossary_model
    listing = "\n".join(f"- {abbr}: {full}" for abbr, full in abbrs.items())
    request = CompletionRequest(
        messages=[
            Message(
                role="user",
                content=(
                    f"보고서 주제: {state.topic}\n\n약어 목록(약어: 본문 병기 풀네임):\n{listing}"
                ),
            )
        ],
        model=model,
        system=_SYSTEM,
        temperature=0.0,
        max_tokens=DEFAULT_MAX_TOKENS,
        cache_key=None,
    )
    with token_context(
        user_id=state.user_id, project_id=state.project_id, operation="assemble.glossary"
    ):
        response = await client.complete(request)
    manifest: dict[str, Any] = _parse_manifest(response.content)
    descs = manifest.get("glossary") if isinstance(manifest.get("glossary"), dict) else {}
    return {
        abbr: {"full": full, "desc": str(descs.get(abbr) or "").strip()[:60]}
        for abbr, full in abbrs.items()
    }


async def persist_glossary(project_id: UUID, glossary: dict[str, dict[str, str]] | None) -> None:
    """projects.glossary 저장 — 요청 밖 경로라 자체 세션(opener)으로 커밋."""
    from sqlalchemy import update

    from src.db.models.project import Project
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        await session.execute(
            update(Project).where(Project.id == project_id).values(glossary=glossary)
        )
        await session.commit()
