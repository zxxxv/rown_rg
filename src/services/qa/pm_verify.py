"""PM 검증 — assemble 직후 챕터당 1콜로 문서 횡단 일관성 경고를 수집 (차단 아님).

정적 게이트가 결정적으로 잡는 것(인용 해석·렌더·수치 근거·분량)과 달리, 여기서는
문서를 가로지르는 문제를 pm_verify_system 역할 프롬프트로 검사한다:
절 간 수치·용어 충돌, 법령 시점 상충(critical), 선행 챕터와 통계 중복 인용 등.

원칙(QA 게이트 결정과의 정합):
- 판정은 사람 몫 — 결과는 경고 리포트(verify_findings)로 저장만 하고,
  파이프라인을 멈추거나 재작성을 트리거하지 않는다(무한성 캡 유지).
- 비용 캡 = 챕터 수 × 1콜. 실패는 호출부(stages)가 삼킨다(비치명).
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import delete, select

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import SectionDraft, SectionPlan
from src.prompts import load_workflow_role
from src.services.generation.planner import _parse_manifest

logger = structlog.get_logger(__name__)

DEFAULT_MAX_TOKENS = 1500
MAX_FINDINGS_PER_CHAPTER = 20  # 무한성 캡 — 모델이 과잉 지적해도 상한
_MAX_CHAPTER_CHARS = 24_000  # 챕터 본문 입력 상한(비용·컨텍스트 캡)
_MAX_DIGEST_ITEMS = 40

_SEVERITIES = {"critical", "warning"}

# 역할 프롬프트는 검증 항목만 정의 — 출력 계약은 여기서 고정한다.
_FORMAT = (
    "마지막 메시지에 아래 형식의 JSON만 출력한다(설명 문장 없이):\n"
    '```json\n{"findings": [{"severity": "critical|warning", "category": "수치 일관성", '
    '"section": "2.1", "detail": "..."}]}\n```\n'
    "문제가 없으면 빈 배열을 출력한다."
)

# 숫자+단위 토큰 — 선행 챕터 통계 중복 검사용 다이제스트(결정적, LLM 아님).
# '년'은 단위에서 제외한다: 연도(2024년)·기간(3년)은 통계가 아니라 시점 표기라
# 다이제스트에 실으면 모델이 "2024년 재등장"을 중복 인용으로 오판한다
# (2026-08-03 실측: 경고 25건 중 12건이 이 노이즈).
_NUM_RE = re.compile(r"\d[\d,.]*\s?(?:%|억\s?원|조\s?원|만\s?원|억|조|만|명|건|개소|개|배|%p|p)")

# 후처리 안전망 — 모델이 그래도 연도만 문제 삼은 '중복 인용' 경고를 내면 버린다.
_QUOTED_RE = re.compile(r"['\"‘’“”]([^'\"‘’“”]{1,24})['\"‘’“”]")
_YEAR_ONLY_RE = re.compile(r"^\d{4}년?$")


def _is_year_only_duplicate(category: str, detail: str) -> bool:
    """중복 인용 계열 경고인데 인용된 값이 전부 연도(YYYY[년])뿐이면 True."""
    if "중복" not in category:
        return False
    quoted = _QUOTED_RE.findall(detail)
    return bool(quoted) and all(_YEAR_ONLY_RE.match(q.strip()) for q in quoted)


def numeric_digest(texts: list[str], cap: int = _MAX_DIGEST_ITEMS) -> list[str]:
    """본문들에서 숫자+단위 토큰을 등장 순서대로 중복 없이 추출 (상한 cap)."""
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        for m in _NUM_RE.finditer(text):
            token = m.group().strip()
            if token not in seen:
                seen.add(token)
                out.append(token)
                if len(out) >= cap:
                    return out
    return out


def _group_by_chapter(state: ProjectState) -> list[tuple[int, list[tuple[SectionPlan, str]]]]:
    """선택 확정 본문을 챕터별로 묶는다 — (chapter_number, [(plan, content)...])."""
    plan_by_id: dict[UUID, SectionPlan] = {p.section_id: p for p in state.section_plan}
    drafts: list[SectionDraft] = state.selected_drafts()
    chapters: dict[int, list[tuple[SectionPlan, str]]] = {}
    for d in drafts:
        plan = plan_by_id.get(d.section_id)
        if plan is None:
            continue
        chapters.setdefault(plan.chapter_number, []).append((plan, d.content))
    return sorted(chapters.items())


def _chapter_input(sections: list[tuple[SectionPlan, str]], prev_digest: list[str]) -> str:
    lines: list[str] = []
    if prev_digest:
        lines.append("선행 챕터에서 이미 인용된 수치(중복 인용 검사용):")
        lines.append(", ".join(prev_digest))
        lines.append("")
    lines.append("검증할 챕터 본문:")
    for plan, content in sections:
        lines.append(f"\n## {plan.chapter_number}.{plan.section_number} {plan.title}\n{content}")
    return "\n".join(lines)[:_MAX_CHAPTER_CHARS]


def _to_rows(chapter_number: int, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """모델 JSON → 저장 행. 형식이 어긋난 항목은 버리고, 상한을 적용한다."""
    rows: list[dict[str, Any]] = []
    for item in manifest.get("findings", []) or []:
        if not isinstance(item, dict):
            continue
        detail = item.get("detail")
        if not (isinstance(detail, str) and detail.strip()):
            continue
        severity = item.get("severity")
        category = item.get("category")
        section = item.get("section")
        category_str = category.strip() if isinstance(category, str) else "기타"
        if _is_year_only_duplicate(category_str, detail):
            continue  # 연도 재언급은 결함이 아니다 — 노이즈 차단
        rows.append(
            {
                "chapter_number": chapter_number,
                "severity": severity if severity in _SEVERITIES else "warning",
                "category": category_str[:40],
                "section_ref": section.strip()[:20]
                if isinstance(section, str) and section.strip()
                else None,
                "detail": detail.strip(),
            }
        )
        if len(rows) >= MAX_FINDINGS_PER_CHAPTER:
            break
    return rows


async def verify_report(
    state: ProjectState,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """선택 확정 본문을 챕터당 1콜로 검증해 경고 행 목록을 돌려준다 (저장은 안 함)."""
    client = client or get_llm_client()
    model = model or settings.verify_model
    system = f"{load_workflow_role('pm_verify_system')}\n\n{_FORMAT}"

    rows: list[dict[str, Any]] = []
    seen_texts: list[str] = []  # 선행 챕터 본문 누적 — 통계 중복 검사 다이제스트 원천
    for chapter_number, sections in _group_by_chapter(state):
        request = CompletionRequest(
            messages=[
                Message(role="user", content=_chapter_input(sections, numeric_digest(seen_texts)))
            ],
            model=model,
            system=system,
            temperature=0.0,
            max_tokens=DEFAULT_MAX_TOKENS,
            cache_key=None,
        )
        with token_context(
            user_id=state.user_id,
            project_id=state.project_id,
            operation=f"qa.pm_verify:{chapter_number}",
        ):
            response = await client.complete(request)
        rows.extend(_to_rows(chapter_number, _parse_manifest(response.content)))
        seen_texts.extend(content for _, content in sections)
    return rows


async def ledger_join_findings(project_id: UUID) -> list[dict[str, Any]]:
    """절 meta의 사실 대장을 문서 단위로 취합해 절 간 지표 충돌을 조인으로 잡는다.

    적립은 write 루프가 절 완료마다 했고(services/ledger.extract_entries), 여기는
    읽기·조인만 한다 — 검출기·주입 이중 투자 금지(단일 원천). 엔트리의 chunk_ids는
    verify_findings 스키마에 자리가 없어 행에는 안 싣는다(근거 연결 UI는 2차).
    """
    from src.db.models.section import Section
    from src.db.session import async_session_maker
    from src.services.ledger import join_conflicts

    async with async_session_maker() as session:
        metas = (
            (
                await session.execute(
                    select(Section.meta).where(
                        Section.project_id == project_id, Section.content != ""
                    )
                )
            )
            .scalars()
            .all()
        )
    entries = [e for meta in metas for e in (meta or {}).get("ledger_entries") or []]
    out: list[dict[str, Any]] = []
    for f in join_conflicts(entries):
        try:
            chapter = int(str(f["section_ref"]).split(".")[0])
        except (ValueError, IndexError):
            chapter = 0
        out.append(
            {
                "chapter_number": chapter,
                "severity": f["severity"],
                "category": f["category"],
                "section_ref": f["section_ref"],
                "detail": f["detail"],
            }
        )
    if out:
        logger.info("pm_verify.ledger_conflicts", project_id=str(project_id), n=len(out))
    return out


async def persist_findings(project_id: UUID, rows: list[dict[str, Any]]) -> None:
    """프로젝트 단위 전량 교체 저장 — 재실행 시 stale 경고가 남지 않는다."""
    from src.db.models.verify_finding import VerifyFinding
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        await session.execute(delete(VerifyFinding).where(VerifyFinding.project_id == project_id))
        for row in rows:
            session.add(VerifyFinding(project_id=project_id, **row))
        await session.commit()


async def run_pm_verify(state: ProjectState, *, model: str | None = None) -> int:
    """stages 진입점 — 검증 후 저장, 경고 수를 돌려준다.

    LLM 경고에 근거 대조 경고(결정적)를 합쳐 저장한다. 둘은 보는 축이 다르다 —
    LLM은 문서 횡단 충돌을, 근거 대조는 "인용한 자리에 그 내용이 없음"을 본다.
    근거 대조가 실패해도 LLM 경고는 남긴다(부가 신호가 본체를 죽이지 않게).
    """
    rows = await verify_report(state, model=model)
    try:
        from src.services.qa.evidence_findings import evidence_findings

        # 판정 모델은 따로 둔다 - 문서 횡단 검증과 달리 짧은 대조라 저가 모델로 충분하다
        rows.extend(await evidence_findings(state.project_id, user_id=state.user_id))
    except Exception:
        logger.warning("pm_verify.evidence_failed", project_id=str(state.project_id), exc_info=True)
    try:
        # 사실 대장 조인 — "같은 지표 다른 값"은 절 내부 어떤 검사도 못 본다(3차 런
        # 실측: CCA 탄소가격 60 vs 55, 각 절이 자기 출처에 충실). 결정적 대조라 LLM
        # 비용 0이고, 실패해도 본체 경고는 남긴다.
        rows.extend(await ledger_join_findings(state.project_id))
    except Exception:
        logger.warning("pm_verify.ledger_failed", project_id=str(state.project_id), exc_info=True)
    await persist_findings(state.project_id, rows)
    n_critical = sum(1 for r in rows if r["severity"] == "critical")
    logger.info(
        "pm_verify.done",
        project_id=str(state.project_id),
        n_findings=len(rows),
        n_critical=n_critical,
    )
    return len(rows)
