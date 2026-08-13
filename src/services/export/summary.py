"""요약문 빌더 — 조립 시 1콜로 장별 압축 요약을 생성해 projects.config["summary"]에 저장.

실납품 보고서 실측(2026-08-11) 관례: 요약문은 표지 뒤·목차 앞에 놓이고, 장마다
"(라벨) 한 문장" 몇 줄로 본문을 압축한다. 절 단위로 작성되는 파이프라인에서는
완성 본문을 아는 조립 단계만 이 요약을 만들 수 있다. 실패는 비치명 — 요약문 없이
렌더한다. config에 영속화해 다운로드 재렌더(순수 코드)에서도 같은 요약을 쓴다.
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

# 장별 요약 입력으로 절 앞부분만 자른다 — 절 서두가 결론 리드 문장(작성 규칙)이라
# 요약 재료로 충분하고, 전체 본문(10만자+)을 넣으면 컨텍스트·비용이 낭비된다.
_SECTION_HEAD_CHARS = 700
_MAX_LINES_PER_CHAPTER = 6
DEFAULT_MAX_TOKENS = 3000

_SYSTEM = (
    "너는 (주)로운인사이트의 보고서 편집자다. 보고서의 장별 절 발췌를 받아 보고서"
    " 맨 앞에 실을 요약문을 만든다.\n"
    '- 장마다 2~5줄, 각 줄은 "(라벨) 한 문장" 형식이다. 라벨은 (배경) (필요성) (목표)'
    " (주요 내용) (시사점)처럼 2~6자 명사구.\n"
    "- 문장은 명사형 종결(~임, ~함, ~됨, ~필요, ~전망)로 쓰고 끝에 마침표를 찍지 않는다.\n"
    "- 발췌에 있는 사실·수치만 쓴다. 새 수치·주장을 만들지 않는다.\n"
    "- 마지막에 JSON만 출력한다: ```json\n"
    '{"chapters": [{"number": 1, "title": "장 제목", "lines": ["(배경) ..."]}]}\n```'
)


def _chapter_excerpts(state: ProjectState) -> tuple[str, dict[int, str]]:
    """선택 확정 본문 → 장별 발췌 텍스트와 장 제목 맵."""
    from src.services.export.report import _chapter_titles, _strip_citations

    drafts = {d.section_id: d for d in state.selected_drafts()}
    ch_titles = _chapter_titles(state)
    parts: list[str] = []
    current: int | None = None
    for plan in state.section_plan:
        draft = drafts.get(plan.section_id)
        if draft is None:
            continue
        if plan.chapter_number != current:
            current = plan.chapter_number
            title = ch_titles.get(plan.chapter_number, "")
            parts.append(f"\n## 제{plan.chapter_number}장 {title}".rstrip())
        head = _strip_citations(draft.content)[:_SECTION_HEAD_CHARS]
        parts.append(f"### {plan.chapter_number}.{plan.section_number} {plan.title}\n{head}")
    return "\n".join(parts).strip(), ch_titles


def _valid_chapter(raw: Any, ch_titles: dict[int, str]) -> dict[str, Any] | None:
    """모델 응답 1건 → {number, title, lines} (형식이 어긋나면 None)."""
    if not isinstance(raw, dict):
        return None
    number = raw.get("number")
    lines = raw.get("lines")
    if not isinstance(number, int) or not isinstance(lines, list):
        return None
    texts = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if not texts:
        return None
    title = str(raw.get("title") or "").strip() or ch_titles.get(number, "")
    return {"number": number, "title": title, "lines": texts[:_MAX_LINES_PER_CHAPTER]}


async def build_summary(
    state: ProjectState,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """{"chapters": [{number, title, lines}]} 생성 — 본문이 없으면 None."""
    excerpts, ch_titles = _chapter_excerpts(state)
    if not excerpts:
        return None
    client = client or get_llm_client()
    model = model or settings.glossary_model  # 요약도 편집 보조 — 최저가 모델을 같이 쓴다
    request = CompletionRequest(
        messages=[
            Message(
                role="user",
                content=(f"보고서 주제: {state.topic}\n\n장별 절 발췌:\n{excerpts}"),
            )
        ],
        model=model,
        system=_SYSTEM,
        temperature=0.0,
        max_tokens=DEFAULT_MAX_TOKENS,
        cache_key=None,
    )
    with token_context(
        user_id=state.user_id, project_id=state.project_id, operation="assemble.summary"
    ):
        response = await client.complete(request)
    manifest: dict[str, Any] = _parse_manifest(response.content)
    raw_chapters = manifest.get("chapters")
    if not isinstance(raw_chapters, list):
        return None
    chapters = [c for c in (_valid_chapter(r, ch_titles) for r in raw_chapters) if c is not None]
    return {"chapters": chapters} if chapters else None


async def persist_summary(project_id: UUID, summary: dict[str, Any]) -> None:
    """projects.config["summary"] 저장 — 다운로드 재렌더가 순수 코드로 같은 요약을 쓴다."""
    from src.db.models.project import Project
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return
        config = dict(project.config or {})
        config["summary"] = summary
        project.config = config
        await session.commit()
