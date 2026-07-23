"""섹션 플래너 — topic·보고서 종류로 목차(SectionPlan)를 LLM으로 설계 (생성만, 판정 없음).

research 앞에서 실행된다: 목차가 웹 수집(ResearchSpec.outline)의 입력이 되므로
자료 수집보다 먼저 만들어져야 수집 품질이 산다. 출력은 JSON 매니페스트로 받아
SectionPlan으로 검증한다. 파싱 실패·빈 목차는 ValueError — 호출부(파이프라인)가
실패로 처리하고, 사람은 재실행으로 복구한다.

report_type이 프리셋 카탈로그(src.prompts)의 키면 toc_system 역할 프롬프트와
프리셋 골격(챕터 구조·서술 방향)을 주입해 주제 특화 목차를 설계하고, 섹션별
담당 분석 에이전트를 프리셋에서 결정적으로 배정한다. 카탈로그에 없는 값이면
기존 자유 목차 설계로 동작한다.

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
from src.prompts import ReportPreset, load_preset, load_workflow_role

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096  # 최대 프리셋(35섹션) 매니페스트가 2048을 넘을 수 있어 여유 확보
MAX_SECTIONS = 40  # 무한성 캡 — 초과분은 버리고 경고 (최대 프리셋: 예비타당성조사 35섹션)

_MANIFEST_FORMAT = (
    "마지막 메시지에 아래 형식의 JSON만 출력한다(설명 문장 없이):\n"
    '```json\n{"sections": [{"chapter": 1, "section": 1, "title": "...", '
    '"direction": "...", "key_points": ["..."]}]}\n```'
)

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
    """매니페스트 → SectionPlan 목록. 형식이 어긋난 항목은 버린다.

    direction·key_points는 프리셋 경로에서만 나오는 부가 정보라 형식이 어긋나면
    항목을 버리지 않고 기본값으로 둔다.
    """
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
        direction = item.get("direction")
        key_points = item.get("key_points")
        plan.append(
            SectionPlan(
                chapter_number=chapter,
                section_number=section,
                title=title.strip(),
                direction=direction.strip() if isinstance(direction, str) else "",
                key_points=[k for k in key_points if isinstance(k, str)]
                if isinstance(key_points, list)
                else [],
            )
        )
    return plan


def plan_from_outline(outline: dict[str, Any]) -> list[SectionPlan]:
    """사용자 확정 목차(config.outline) → SectionPlan. LLM 호출 없는 결정적 경로.

    생성 화면에서 프리셋 골격을 펼쳐 편집·확정한 목차다 — 사용자가 본 것과 실행
    결과가 정확히 일치해야 하므로 LLM을 태우지 않는다. 장·절 번호는 배열 위치
    (1-base)에서 파생한다 — 편집·재정렬과 번호가 어긋날 수 없다. 제목 없는 절은
    버리고, 유효 섹션이 없으면 ValueError(빈 목차로 후속 단계를 돌리지 않는다).
    """
    plan: list[SectionPlan] = []
    chapters = outline.get("chapters") if isinstance(outline, dict) else None
    for ci, chapter in enumerate(chapters or [], start=1):
        if not isinstance(chapter, dict):
            continue
        sections = chapter.get("sections")
        for si, sec in enumerate(sections if isinstance(sections, list) else [], start=1):
            if not isinstance(sec, dict):
                continue
            title = sec.get("title")
            if not (isinstance(title, str) and title.strip()):
                continue
            direction = sec.get("direction")
            key_points = sec.get("key_points")
            analysts = sec.get("analysts")
            plan.append(
                SectionPlan(
                    chapter_number=ci,
                    section_number=si,
                    title=title.strip(),
                    direction=direction.strip() if isinstance(direction, str) else "",
                    key_points=[k for k in key_points if isinstance(k, str)]
                    if isinstance(key_points, list)
                    else [],
                    analysts=[a for a in analysts if isinstance(a, str)]
                    if isinstance(analysts, list)
                    else [],
                )
            )
    if not plan:
        raise ValueError("outline에 유효한 섹션이 없습니다")
    if len(plan) > MAX_SECTIONS:
        logger.warning("planner.outline_truncated", planned=len(plan), cap=MAX_SECTIONS)
        plan = plan[:MAX_SECTIONS]
    return plan


def _resolve_preset(report_type: str) -> ReportPreset | None:
    """report_type이 프리셋 카탈로그 키면 프리셋, 아니면 None(자유 목차 경로)."""
    try:
        return load_preset(report_type)
    except KeyError:
        return None


def _preset_skeleton(preset: ReportPreset) -> dict[str, Any]:
    """LLM에 줄 프리셋 골격 — 에이전트 배정은 코드가 하므로 agents는 제외한다."""
    return {
        "name": preset.name,
        "domain_context": preset.domain_context,
        "chapters": [
            {
                "title": ch.title,
                "sections": [
                    {"title": s.title, "direction": s.direction, "key_points": s.key_points}
                    for s in ch.sections
                ],
            }
            for ch in preset.chapters
        ],
    }


def _assign_analysts(plan: list[SectionPlan], preset: ReportPreset) -> list[SectionPlan]:
    """생성 목차의 챕터를 프리셋 챕터에 위치로 대응시켜 담당 분석 에이전트를 배정.

    toc_system 규칙상 챕터 수·순서는 프리셋과 동일하게 유지되므로 챕터는 번호로,
    챕터 안의 섹션은 등장 순서(초과분은 마지막 섹션)로 매핑한다 — LLM 판단이 아닌
    결정적 배정. 프리셋 범위 밖 챕터는 배정 없이 둔다.
    """
    counters: dict[int, int] = {}
    assigned: list[SectionPlan] = []
    for sp in plan:
        idx = counters.get(sp.chapter_number, 0)
        counters[sp.chapter_number] = idx + 1
        if 1 <= sp.chapter_number <= len(preset.chapters):
            sections = preset.chapters[sp.chapter_number - 1].sections
            agents = sections[min(idx, len(sections) - 1)].agents
            sp = sp.model_copy(update={"analysts": list(agents)})
        assigned.append(sp)
    return assigned


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

    report_type이 프리셋 카탈로그 키면 toc_system+프리셋 골격으로 설계하고
    섹션별 담당 분석 에이전트를 배정한다. 유효한 섹션이 하나도 없으면
    ValueError — 빈 목차로 후속 단계(수집·작성)를 돌리는 것보다 실패가 낫다.
    """
    client = client or get_llm_client()
    preset = _resolve_preset(report_type)
    if preset is not None:
        system = f"{load_workflow_role('toc_system')}\n\n{_MANIFEST_FORMAT}"
        user = json.dumps({"topic": topic, "preset": _preset_skeleton(preset)}, ensure_ascii=False)
    else:
        system = _SYSTEM
        user = json.dumps({"topic": topic, "report_type": report_type}, ensure_ascii=False)
    request = CompletionRequest(
        model=model,
        system=system,
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
    if preset is not None:
        plan = _assign_analysts(plan, preset)
    logger.info("planner.done", topic=topic, n_sections=len(plan), preset=preset and preset.name)
    return plan
