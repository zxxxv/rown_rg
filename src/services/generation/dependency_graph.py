"""절 사이 의존 그래프 초안 — "이 절은 앞 절의 결과가 있어야 쓸 수 있다"를 AI가 뽑는다.

**왜 AI인가**: 지금까지 builds_on은 프리셋에 손으로 박아 넣거나(예타 146절 중 5절) 사람이
목차 설계 화면에서 일일이 등록해야 했다. 그래서 사실상 죽은 필드였다 — v6 런에서 주입
적립 0건. 사람이 안 적으면 논지가 안 이어지고, 적으라고 하면 안 적는다.

**순서와 같은 것이다**: 작성 루프는 이미 이 그래프로 순서를 정한다(write_loop). 의존이
있는 절은 앞 절이 끝나기를 기다리고, 없는 절끼리는 병렬로 쓴다. 그래서 "어디를 순차로
쓸 것인가"와 "어느 절이 앞 절을 이어받는가"는 **한 질문**이다. 따로 두면 사람이 두 번
답해야 하고, 둘이 어긋나면 순서만 맞고 내용은 안 이어진다.

**인색하게 뽑는다**: 실측이 경고한다 — 서사 사슬을 넉넉히 걸었더니 무근거 서술이 39%
늘었다(2026-08-06 계층 작성 실험). 프리셋이 손으로 적어 둔 5건도 전부 "소결·시사점 절이
같은 장을 받는다" 꼴이다. 그래서 프롬프트가 "대부분의 절은 독립"임을 못 박고, 절당 상한을
둔다. 실패하면 빈 그래프다 — 종전과 똑같이 평면 병렬로 쓴다(막지 않는다).
"""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.builds_on import MAX_REFS_PER_SECTION
from src.core.types import SectionPlan

logger = structlog.get_logger(__name__)

# 의존 그래프는 절 나열→간선 목록이라 짧다 - 목차 35절(예타)도 1500이면 남는다.
_MAX_TOKENS = 1500

_SYSTEM = (
    "당신은 보고서 목차를 읽고 **절 사이의 의존 관계**만 뽑는 편집자다.\n"
    "의존이란 '그 절을 쓰려면 앞 절이 낸 결론·수치가 반드시 있어야 한다'는 뜻이다.\n"
    "\n"
    "규칙:\n"
    "- **대부분의 절은 독립이다.** 같은 주제를 다룬다는 이유만으로 의존이 아니다.\n"
    "- 전형적인 의존은 소결·시사점·종합·결론·권고처럼 **앞의 것을 모아 판단하는 절**이다.\n"
    "- 앞 절만 가리킨다. 뒤 절이나 자기 자신은 가리킬 수 없다.\n"
    f"- 한 절이 가리킬 수 있는 대상은 최대 {MAX_REFS_PER_SECTION}개다.\n"
    "- 장 전체를 받으려면 '2.*'처럼 쓴다. 특정 절이면 '6.2'처럼 쓴다.\n"
    "- 의존이 없으면 그 절은 결과에 넣지 말라.\n"
    "\n"
    '출력은 JSON만: {"deps": [{"section": "5.3", "builds_on": ["5.1", "5.2"]}]}'
)


def _outline_text(plan: list[SectionPlan]) -> str:
    lines: list[str] = []
    last_ch: int | None = None
    for s in plan:
        if s.chapter_number != last_ch:
            lines.append(f"{s.chapter_number}장 {s.chapter_title or ''}".rstrip())
            last_ch = s.chapter_number
        lines.append(f"  {s.chapter_number}.{s.section_number} {s.title}")
    return "\n".join(lines)


def _parse(raw: str) -> dict[str, list[str]]:
    """모델 응답 → {절 라벨: [참조 라벨]}. 못 읽으면 빈 dict(막지 않는다)."""
    text = raw.strip()
    if "```" in text:  # 펜스로 감싸 오는 모델이 있다
        parts = [p for p in text.split("```") if "{" in p]
        text = parts[0] if parts else text
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    out: dict[str, list[str]] = {}
    for item in data.get("deps") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("section") or "").strip()
        refs = item.get("builds_on")
        if not label or not isinstance(refs, list):
            continue
        out[label] = [str(r).strip() for r in refs if str(r).strip()]
    return out


def sanitize(plan: list[SectionPlan], drafted: dict[str, list[str]]) -> dict[str, list[str]]:
    """모델이 낸 그래프를 목차에 대고 정화한다 (순수 함수).

    유령 절·자기 참조·**뒤를 가리키는 참조**·상한 초과를 버린다. 뒤 참조를 버리는 이유는
    작성 순서와 한 몸이기 때문이다 — 뒤를 기다리면 교착이고, 안 기다리면 빈 주입이라
    어느 쪽도 얻는 게 없다.
    """
    pos = {f"{s.chapter_number}.{s.section_number}": i for i, s in enumerate(plan)}
    chapters = {s.chapter_number for s in plan}
    # 장 참조("2.*")는 그 장의 마지막 절 위치로 견준다 — 그 장이 다 끝나야 받을 수 있다.
    chapter_end = {
        ch: max(i for i, s in enumerate(plan) if s.chapter_number == ch) for ch in chapters
    }

    out: dict[str, list[str]] = {}
    for label, refs in drafted.items():
        here = pos.get(label)
        if here is None:
            continue
        kept: list[str] = []
        for ref in refs:
            if ref == label:
                continue
            if ref.endswith(".*"):
                try:
                    ch = int(ref[:-2])
                except ValueError:
                    continue
                if ch not in chapters or chapter_end[ch] >= here:
                    continue
            else:
                there = pos.get(ref)
                if there is None or there >= here:
                    continue
            if ref not in kept:
                kept.append(ref)
            if len(kept) >= MAX_REFS_PER_SECTION:
                break
        if kept:
            out[label] = kept
    return out


async def draft_builds_on(
    plan: list[SectionPlan],
    *,
    client: LLMClient | None = None,
    model: str,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> dict[str, list[str]]:
    """목차 하나에 LLM 1콜 — {절 라벨: [앞 절 라벨]}. 실패하면 빈 dict.

    입력은 번호와 제목뿐이라 35절짜리 예타도 한 화면이다(비용 무시할 수준).
    """
    if len(plan) < 2:
        return {}
    client = client or get_llm_client()
    prompt = (
        "다음 목차에서 의존 관계를 뽑아라.\n\n"
        f"{_outline_text(plan)}\n\n"
        "의존이 없는 절은 넣지 말 것. JSON만 출력."
    )
    try:
        with token_context(user_id=user_id, project_id=project_id, operation="plan.dependencies"):
            resp = await client.complete(
                CompletionRequest(
                    model=model,
                    messages=[Message(role="user", content=prompt)],
                    system=_SYSTEM,
                    max_tokens=_MAX_TOKENS,
                    temperature=0.0,
                )
            )
        graph = sanitize(plan, _parse(resp.content))
    except Exception:
        logger.warning(
            "plan.dependencies_failed",
            project_id=str(project_id) if project_id else None,
            exc_info=True,
        )
        return {}
    logger.info(
        "plan.dependencies_drafted",
        project_id=str(project_id) if project_id else None,
        n_sections=len(plan),
        n_linked=len(graph),
    )
    return graph
