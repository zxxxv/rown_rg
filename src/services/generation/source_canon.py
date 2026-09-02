"""정본 배정 — 자료 확정 후, 자료명 수준의 서술 담당을 정하는 2층(LLM 1콜).

1층(설계 브리프의 topic_ownership)은 **구조 수준**만 다룬다 — 설계 시점엔 웹 수집이
아직 없고, 업로드도 자료 검토 게이트에서 제외될 수 있어 자료명 배정이 유령을 가리킬
수 있다(2026-08-21 사용자 지적: 순서가 이상하다). 이 모듈은 자료 검토가 끝난 **확정
코퍼스**(is_included)를 보고 "이 자료의 수치·문항·기준은 이 절이 정본"을 배정해
config["_design_plan"]의 owns/foreign_topics에 병합한다 — 작성 직전이라 유령 배정이
구조적으로 불가능한 시점이다.

원칙은 brief_ai와 같다: 실패는 0건(작성은 1층 배정만으로 계속), 산출은 목차에 대조해
아는 절만 남긴다.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import create_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.state import ProjectState

logger = structlog.get_logger(__name__)

MAX_CANON_TOPICS = 10
MAX_CANON_TOPIC_CHARS = 80
_FIELD_MAX_CHARS = 2000  # runner._PLAN_FIELD_MAX_CHARS와 동일 — 프롬프트 폭주 방지
_MAX_TOKENS = 2500

SYSTEM_PROMPT = """너는 보고서 작성 직전, 확정된 자료 목록을 절에 배정하는 편집 책임자다.
입력은 JSON — sections(절 번호·제목·방향·이미 배정된 구조 수준 담당)과
sources(확정된 자료 목록: 제목·유형·쪽수)다.

과제: 여러 절이 같은 자료를 되풀이 인용·재서술할 위험이 큰 자료를 골라, 그 자료의
핵심 내용(수치·문항·기준·추정치)을 **가장 적합한 절 하나**에 정본으로 배정하라(3~10건).
- topic은 반드시 자료 제목을 포함해 "『자료명』의 무엇" 형태로 구체적으로 쓴다.
  ("현황"·"배경" 같은 일반어만으로 된 topic 금지)
- 이미 구조 수준 담당(owns)이 있는 절과 어긋나지 않게 배정하라 — 같은 소재의 구조
  담당 절이 있으면 그 절이 자료 정본도 가져간다.
- 한 자료를 여러 절에 쪼개 배정해도 된다(예: 수치는 1.3, 정책 시사점은 3.5).
- 어떤 절과도 무관하거나 배경 참고용인 자료는 배정하지 않는다.

마지막에 아래 형태의 JSON만 출력한다(설명 문장 없이):
{"source_canon":[{"topic":"『OO 실태조사』의 업종별 대응 수치","owner":"1.3"}]}"""


def _compact_input(sections: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    return json.dumps({"sections": sections, "sources": sources}, ensure_ascii=False)


def _extract_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_assignments(raw: dict[str, Any], known_labels: set[str]) -> list[dict[str, str]]:
    """LLM 산출 → [{topic, owner}]. 유령 절·빈 토픽·중복 토픽은 버린다."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for t in raw.get("source_canon") or []:
        if not isinstance(t, dict):
            continue
        topic = " ".join(str(t.get("topic") or "").split())[:MAX_CANON_TOPIC_CHARS]
        owner = str(t.get("owner") or "").strip()
        key = topic.lower()
        if not topic or owner not in known_labels or key in seen:
            continue
        seen.add(key)
        out.append({"topic": topic, "owner": owner})
        if len(out) >= MAX_CANON_TOPICS:
            break
    return out


def merge_into_notes(
    notes: dict[str, Any],
    assignments: list[dict[str, str]],
    label_to_sid: dict[str, str],
) -> dict[str, Any]:
    """배정을 owns/foreign_topics에 병합한 새 notes를 돌려준다(원본 불변).

    소유 절엔 정본 목록을 덧붙이고, 나머지 전 절엔 금지 목록을 덧붙인다 —
    runner._commit_design_plan과 같은 계약이라 작성 프롬프트 렌더러(design_plan_note)를
    그대로 재사용한다.
    """
    merged = {sid: dict(note) for sid, note in notes.items() if isinstance(note, dict)}
    for label, sid in label_to_sid.items():
        if sid not in merged:
            merged[sid] = {}
    for label, sid in label_to_sid.items():
        note = merged[sid]
        own_add = " · ".join(a["topic"] for a in assignments if a["owner"] == label)
        foreign_add = " · ".join(
            f"{a['topic']}({a['owner']}절 소관)" for a in assignments if a["owner"] != label
        )
        if own_add:
            note["owns"] = _dedup_join(str(note.get("owns") or ""), own_add)[:_FIELD_MAX_CHARS]
        if foreign_add:
            note["foreign_topics"] = _dedup_join(
                str(note.get("foreign_topics") or ""), foreign_add
            )[:_FIELD_MAX_CHARS]
    return {sid: note for sid, note in merged.items() if any(note.values())}


def _dedup_join(base: str, addition: str) -> str:
    """' · ' 목록 병합 — 같은 항목이 라운드마다 다시 붙지 않게 한다.

    2026-09-03 수리: 배정이 재실행(게이트 재개방·재시작)될 때마다 같은 자료명 배정이
    통째로 덧붙어, 철강 _design_plan owns에 동일 항목이 6중으로 쌓여 작성 프롬프트를
    같은 지시 6줄로 오염시켰다.
    """
    seen: list[str] = []
    combined = f"{base} · {addition}" if base.strip() else addition
    for part in combined.split(" · "):
        p = part.strip()
        if p and p not in seen:
            seen.append(p)
    return " · ".join(seen)


async def _confirmed_sources(project_id) -> list[dict[str, Any]]:
    """자료 검토가 확정한 코퍼스(is_included) — 제목·유형·쪽수만."""
    from sqlalchemy import select

    from src.db.models.project_source import ProjectSource
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(
                    ProjectSource.title, ProjectSource.source_type, ProjectSource.metadata_
                ).where(
                    ProjectSource.project_id == project_id,
                    ProjectSource.is_included.is_(True),
                )
            )
        ).all()
    return [
        {
            "title": (t or "")[:100],
            "type": st,
            "pages": (m or {}).get("page_count"),
        }
        for t, st, m in rows
    ][:60]


async def refresh_source_canon(
    state: ProjectState,
    *,
    model: str,
    client: LLMClient | None = None,
) -> int:
    """확정 코퍼스 기준 정본 배정을 만들어 _design_plan에 병합. 반환=배정 건수.

    호출 시점은 작성 진입 직전(stages.write) — 자료 검토·색인이 끝나 코퍼스가
    확정된 뒤다. 병합은 state.options(작성이 읽는 곳)와 DB config 양쪽에 반영한다.
    """
    sources = await _confirmed_sources(state.project_id)
    if not sources:
        return 0
    notes_raw = state.options.get("_design_plan") if isinstance(state.options, dict) else None
    notes: dict[str, Any] = notes_raw if isinstance(notes_raw, dict) else {}
    label_to_sid = {
        f"{p.chapter_number}.{p.section_number}": str(p.section_id) for p in state.section_plan
    }
    sections_input = []
    for p in state.section_plan:
        label = f"{p.chapter_number}.{p.section_number}"
        note = notes.get(str(p.section_id)) or {}
        sections_input.append(
            {
                "label": label,
                "title": p.title,
                "direction": (p.direction or "")[:200],
                "owns": str(note.get("owns") or "")[:300],
            }
        )

    llm = client or create_llm_client()
    request = CompletionRequest(
        model=model,
        system=SYSTEM_PROMPT,
        messages=[Message(role="user", content=_compact_input(sections_input, sources))],
        max_tokens=_MAX_TOKENS,
        temperature=0.2,
    )
    with token_context(
        user_id=state.user_id, project_id=state.project_id, operation="source_canon"
    ):
        response = await llm.complete(request)
    raw = _extract_json(response.content)
    if raw is None:
        logger.warning("source_canon.parse_failed", project_id=str(state.project_id))
        return 0
    assignments = parse_assignments(raw, set(label_to_sid))
    if not assignments:
        return 0

    merged = merge_into_notes(notes, assignments, label_to_sid)
    if isinstance(state.options, dict):
        state.options["_design_plan"] = merged

    # DB에도 반영 — 재개·화면이 같은 계약을 보게 한다. 실패해도 메모리 반영은 유효.
    try:
        from src.db.models.project import Project
        from src.db.session import async_session_maker

        async with async_session_maker() as session:
            project = await session.get(Project, state.project_id)
            if project is not None:
                project.config = {**(project.config or {}), "_design_plan": merged}
                await session.commit()
    except Exception:
        logger.warning(
            "source_canon.persist_failed", project_id=str(state.project_id), exc_info=True
        )

    logger.info(
        "source_canon.assigned",
        project_id=str(state.project_id),
        n_assignments=len(assignments),
        topics=[a["topic"] for a in assignments],
    )
    return len(assignments)
