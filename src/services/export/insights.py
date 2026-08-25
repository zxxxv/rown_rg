"""시사점 요약 빌더 — 조립 시 1콜로 2~3쪽 브리핑을 만들어 projects.insights에 저장.

본문의 시사점·제언 절은 그 자체로 3~5쪽이라(프리셋 min/max_chars 4500~7500) 결정권자가
한눈에 훑기엔 길다. 그 절들을 다시 2~3쪽으로 압축한 별도 산출물을 만든다.

**원본 보고서는 건드리지 않는다** — 이 요약은 HWPX에 실리지 않고 웹 /insights에서만 본다
(2026-08-25 사용자 결정). 그래서 렌더 경로에는 아무 배선도 없다: 안 실리는 게 기본이다.

실패는 비치명 — 요약이 없으면 웹 화면이 "아직 없음"을 보여줄 뿐 렌더·완료를 막지 않는다.
"""

from __future__ import annotations

import re
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

DEFAULT_MAX_TOKENS = 6000
# 입력 상한 — 시사점 절만 모아도 프리셋상 최대 2~3절 × 7,500자다. 여유를 두되
# 무한정 밀어 넣지 않는다(pm_verify가 24,000자 상한에 조용히 잘려 본문 47.6%만
# 보고 판정하던 전례가 있다 — 여기선 넘치면 로그를 남긴다).
MAX_INPUT_CHARS = 60_000
# 2~3쪽 목표. 본문 12pt·줄간격 160%·여백 20/20/15/15 기준 A4 1쪽 ≈ 1,500자.
TARGET_MIN_CHARS = 3_000
TARGET_MAX_CHARS = 4_500

# 시사점 성격의 절 제목 — 프리셋 10종에서 실제로 쓰이는 표기를 그대로 담았다
# (…및 제언 / 종합 시사점 / 사례 분석 시사점 / 핵심 제언 및 Next Step / 결론 …).
_INSIGHT_TITLE_RE = re.compile(r"시사점|제언|결론|소결|Next\s*Step", re.IGNORECASE)

_SYSTEM = (
    "너는 정책·산업 보고서를 결정권자에게 브리핑하는 편집자다. 보고서의 시사점·제언"
    " 부분을 받아 **2~3쪽 분량의 요약 브리핑**으로 압축한다.\n\n"
    "## 규칙\n"
    f"- 전체 {TARGET_MIN_CHARS:,}~{TARGET_MAX_CHARS:,}자. 이 범위를 지켜라.\n"
    "- 개조식으로 쓴다. 계층은 `□`(대) → `ㅇ`(중) → `-`(소) 순서를 지킨다.\n"
    "- **원문에 있는 사실만** 쓴다. 수치·기관명·법령명은 원문 표기 그대로 옮기고,"
    " 원문에 없는 값을 만들지 마라. 근거가 불확실하면 그 항목을 통째로 빼라.\n"
    "- 원문의 (출처 n)·[n] 같은 인용 표식은 옮기지 않는다.\n"
    "- 나열이 아니라 판단을 쓴다. '무엇이 확인됐다'보다 '그래서 무엇을 해야 한다'가"
    " 앞에 오게 하라.\n\n"
    "## 구성\n"
    "1. `## 핵심 요약` — 보고서 전체의 결론을 3~5개 항목으로. 각 항목 2~3줄.\n"
    "2. `## 주요 시사점` — 근거가 붙은 시사점 4~6개. 항목마다 관련 수치를 1개 이상.\n"
    "3. `## 제언` — 실행 가능한 제언 3~5개. 각 항목에 주체와 시점을 명시.\n\n"
    "마지막에 JSON만 출력한다:\n"
    '```json\n{"insights": "## 핵심 요약\\n\\n□ …"}\n```'
)


def collect_insight_sections(state: ProjectState) -> list[tuple[str, str]]:
    """요약의 입력이 될 (절 라벨, 본문) 목록.

    1순위 = 제목이 시사점·제언·결론·소결인 절. 프리셋 10종에서 이 절들이 이미 그
    장(또는 보고서 전체)의 결론을 담도록 작성된다.
    2순위(자유주제 등 1순위가 비었을 때) = 마지막 장의 모든 절.
    """
    drafts = {d.section_id: d.content for d in state.selected_drafts()}
    plans = [p for p in state.section_plan if p.section_id in drafts]
    if not plans:
        return []

    picked = [p for p in plans if _INSIGHT_TITLE_RE.search(p.title)]
    if not picked:
        last_chapter = max(p.chapter_number for p in plans)
        picked = [p for p in plans if p.chapter_number == last_chapter]
    return [
        (f"{p.chapter_number}.{p.section_number} {p.title}", drafts[p.section_id]) for p in picked
    ]


def _build_input(sections: list[tuple[str, str]]) -> str:
    from src.services.export.report import _strip_citations

    lines: list[str] = []
    for label, content in sections:
        lines.append(f"\n## {label}\n{_strip_citations(content)}")
    text = "\n".join(lines)
    if len(text) > MAX_INPUT_CHARS:
        # 조용히 자르지 않는다 — 무엇이 빠졌는지 로그로 남긴다.
        logger.warning(
            "insights.input_truncated", total_chars=len(text), kept_chars=MAX_INPUT_CHARS
        )
        text = text[:MAX_INPUT_CHARS]
    return text


async def build_insights(
    state: ProjectState,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """시사점 2~3쪽 요약 생성 — 입력이 될 절이 없으면 None."""
    sections = collect_insight_sections(state)
    if not sections:
        return None
    client = client or get_llm_client()
    model = model or settings.insights_model

    request = CompletionRequest(
        messages=[
            Message(
                role="user",
                content=(
                    f"보고서 주제: {state.topic}\n\n"
                    "아래는 이 보고서의 시사점·제언 부분이다. 규칙에 따라 요약하라.\n"
                    f"{_build_input(sections)}"
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
        user_id=state.user_id, project_id=state.project_id, operation="assemble.insights"
    ):
        response = await client.complete(request)

    manifest = _parse_manifest(response.content)
    body = str(manifest.get("insights") or "").strip()
    if not body:
        logger.warning("insights.empty_manifest", project_id=str(state.project_id))
        return None
    logger.info(
        "insights.built",
        project_id=str(state.project_id),
        n_sections=len(sections),
        chars=len(body),
    )
    return {
        "content": body,
        "source_sections": [label for label, _ in sections],
        "model": model,
    }


async def persist_insights(project_id: UUID, insights: dict[str, Any] | None) -> None:
    """projects.insights 저장 — 요청 밖 경로라 자체 세션(opener)으로 커밋."""
    from sqlalchemy import update

    from src.db.models.project import Project
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        await session.execute(
            update(Project).where(Project.id == project_id).values(insights=insights)
        )
        await session.commit()
