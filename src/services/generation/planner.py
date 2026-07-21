"""섹션 플래너 — topic·보고서 종류로 목차(SectionPlan)를 LLM으로 설계 (생성만, 판정 없음).

research 앞에서 실행된다: 목차가 웹 수집(ResearchSpec.outline)의 입력이 되므로
자료 수집보다 먼저 만들어져야 수집 품질이 산다. 출력은 JSON 매니페스트로 받아
SectionPlan으로 검증한다. 파싱 실패·빈 목차는 ValueError — 호출부(파이프라인)가
실패로 처리하고, 사람은 재실행으로 복구한다.

무한성 캡: 섹션 수는 MAX_SECTIONS로 상한. 모델이 과대 목차를 내도 잘라낸다.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.types import SectionPlan

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2048
MAX_SECTIONS = 20  # 무한성 캡 — 초과분은 버리고 경고

_SYSTEM = (
    "너는 정부·공공 보고서의 목차 설계자다. "
    "주제와 보고서 종류(JSON)를 받아 장·절 구조의 세부목차를 설계하라.\n"
    "규칙:\n"
    "- 장(chapter)은 3~6개, 각 장의 절(section)은 1~4개로 실무 보고서답게.\n"
    "- 절 제목은 검색 질의로 쓸 수 있게 구체적으로 (예: '고령화 추이와 전망' O, '배경' X).\n"
    "- 마지막 메시지에 아래 형식의 JSON만 출력한다(설명 문장 없이):\n"
    '```json\n{"sections": [{"chapter": 1, "section": 1, "title": "..."}]}\n```'
)


def _parse_manifest(text: str) -> dict[str, Any]:
    """모델 최종 텍스트에서 JSON을 추출. ```json``` 블록 우선, 없으면 마지막 {...}."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced[-1] if fenced else None
    if candidate is None:
        # fence 없는 응답: 첫 '{'~마지막 '}' — 중첩 객체를 통째로 잡는다.
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if not candidate:
        return {}
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _to_plan(manifest: dict[str, Any]) -> list[SectionPlan]:
    """매니페스트 → SectionPlan 목록. 형식이 어긋난 항목은 버린다."""
    plan: list[SectionPlan] = []
    for item in manifest.get("sections", []) or []:
        if not isinstance(item, dict):
            continue
        chapter, section, title = item.get("chapter"), item.get("section"), item.get("title")
        if not (isinstance(chapter, int) and chapter >= 1):
            continue
        if not (isinstance(section, int) and section >= 1):
            continue
        if not (isinstance(title, str) and title.strip()):
            continue
        plan.append(
            SectionPlan(chapter_number=chapter, section_number=section, title=title.strip())
        )
    return plan


async def plan_sections(
    topic: str,
    report_type: str,
    *,
    model: str = DEFAULT_MODEL,
    client: LLMClient | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> list[SectionPlan]:
    """주제·보고서 종류로 목차를 설계해 SectionPlan 목록으로 반환한다.

    유효한 섹션이 하나도 없으면 ValueError — 빈 목차로 후속 단계(수집·작성)를
    돌리는 것보다 실패가 낫다.
    """
    client = client or get_llm_client()
    user = json.dumps({"topic": topic, "report_type": report_type}, ensure_ascii=False)
    request = CompletionRequest(
        model=model,
        system=_SYSTEM,
        messages=[Message(role="user", content=user)],
        max_tokens=max_tokens,
        cache_key=None,
    )
    with token_context(user_id=user_id, project_id=project_id, operation="plan.outline"):
        response = await client.complete(request)

    plan = _to_plan(_parse_manifest(response.content))
    if not plan:
        raise ValueError(f"플래너가 유효한 목차를 반환하지 않음 (topic={topic!r})")
    if len(plan) > MAX_SECTIONS:
        logger.warning("planner.truncated", planned=len(plan), cap=MAX_SECTIONS)
        plan = plan[:MAX_SECTIONS]
    logger.info("planner.done", topic=topic, n_sections=len(plan))
    return plan
