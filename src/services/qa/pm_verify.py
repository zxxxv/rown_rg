"""PM 검증 — assemble 직후 챕터당 1콜로 문서 횡단 일관성 경고를 수집 (차단 아님).

정적 게이트가 결정적으로 잡는 것(인용 해석·렌더·수치 근거·분량)과 달리, 여기서는
LLM이라야 보이는 축만 pm_verify_system 역할 프롬프트로 검사한다:
절 간 수치 충돌·용어 표기 혼용·법령 시점 상충(critical).

축을 이 셋으로 좁힌 이유(2026-08-23 v6 전수 검토): LLM 경고 27건 중 17건이
중복 인용(재언급은 정상)·환각 검출("확인 필요" 추정 — 근거 원문을 못 보는데 판정을
시킨 축)·형식(결정적 절단 검출기 소관)의 노이즈였다. 그 축들은 결정적 검출기와
근거 동봉 판정(evidence_findings)이 더 정확히 본다. 프롬프트의 예외 규칙은 모델이
안 지키므로 코드 필터(_KEPT_AXES·_ASSERT_RE)가 최종 관문이다.

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
from src.core.clock import now as clock_now
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import SectionDraft, SectionPlan
from src.prompts import load_workflow_role
from src.services.generation.planner import _parse_manifest
from src.services.qa.gate import normalize_number, significant_numbers

logger = structlog.get_logger(__name__)

DEFAULT_MAX_TOKENS = 1500
MAX_FINDINGS_PER_CHAPTER = 20  # 무한성 캡 — 모델이 과잉 지적해도 상한
_MAX_CHAPTER_CHARS = 24_000  # 챕터 본문 입력 상한(비용·컨텍스트 캡)
_MAX_DIGEST_ITEMS = 40

_SEVERITIES = {"critical", "warning"}

# 역할 프롬프트는 검증 항목만 정의 — 출력 계약은 여기서 고정한다.
# value_a/value_b를 구조로 강제하는 이유(2026-08-23 일반화 수술): 충돌 여부를
# 모델의 문장 표현(_ASSERT_RE 어휘)으로 판정하면 모델·언어가 바뀔 때 같이 흔들린다.
# 두 값이 필드로 오면 "채워졌고 서로 다른가"라는 구조 판정이 되고, 그 값이 실제
# 본문에 있는지까지 코드가 실증할 수 있다(창작 경고 소거).
_FORMAT = (
    "마지막 메시지에 아래 형식의 JSON만 출력한다(설명 문장 없이):\n"
    '```json\n{"findings": [{"severity": "critical|warning", "category": "수치 일관성", '
    '"section": "2.1", "value_a": "91억 유로", "loc_a": "2.1", '
    '"value_b": "90억 달러", "loc_b": "2.2", "detail": "..."}]}\n```\n'
    "value_a/value_b에는 충돌하는 두 값(또는 두 표기)을 본문 표기 그대로 적는다. "
    "loc_a/loc_b는 그 값이 나온 절 번호(선행 챕터 인용 수치 목록의 값이면 '선행').\n"
    "문제가 없으면 빈 배열을 출력한다."
)

# 숫자+단위 토큰 — 선행 챕터 수치를 다음 챕터 콜에 실어 챕터 간 값 충돌을 보게 하는
# 다이제스트(결정적, LLM 아님). '년'은 단위에서 제외한다: 연도(2024년)·기간(3년)은
# 통계가 아니라 시점 표기다(2026-08-03 실측: 경고 25건 중 12건이 이 노이즈).
_NUM_RE = re.compile(r"\d[\d,.]*\s?(?:%|억\s?원|조\s?원|만\s?원|억|조|만|명|건|개소|개|배|%p|p)")

# 남길 LLM 축 — 프롬프트의 검증 항목과 짝(키워드 포함 판정). 그 밖의 카테고리
# (중복 인용·환각 검출·형식·출처 매칭)는 결정적 검출기·근거 동봉 판정이 더 정확히
# 보는 축이라 저장하지 않는다(2026-08-23 v6 전수 검토: 27건 중 17건이 그 노이즈).
_KEPT_AXES = ("수치", "법령", "용어")

# 충돌 단정 어휘 — 두 값(표기)이 실제로 어긋난다는 주장. value 필드가 없는(구조
# 계약을 어긴) 행에만 쓰는 폴백이다 — 어휘 목록은 모델·표현이 바뀌면 같이 흔들리는
# 과적합 표면이라, 정본 판정은 값 필드의 구조("채워졌고 서로 다른가")로 한다.
_ASSERT_RE = re.compile(r"불일치|상이|상충|충돌|혼용|모순|어긋|다르게|달리")


def _norm_value(s: str) -> str:
    """값 대조용 정규화 — 공백·콤마 제거, 라틴 소문자화. 표기 흔들림에 둔감하게."""
    return re.sub(r"\s+", "", s.replace(",", "")).lower()


# 한국어 수 표기의 크기 동치 — '482.7억'과 '482억 7,000만 달러'는 같은 값이다.
# 다보고서 회귀 실측(2026-08-23, 5유형)에서 PM 경고 45건 중 8건이 이런 '같은 값
# 다른 표기' 지적이었다 — 문자열 정규화로는 못 가르므로 크기로 잰다.
_KOR_UNIT = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}
_NUM_GROUP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(조|억|만|천)?")
_RESIDUE_STRIP = ("조", "억", "만", "천", "여", "약", "이상", "미만", "가량", "수준", "기준")


def _magnitude(s: str) -> float | None:
    """'482억 7,000만' → 4.827e10. 합성 표기는 단위가 내림차순일 때만 한 값으로 본다 —
    '2025년 591억'처럼 별개 수가 이어진 문자열은 크기가 아니므로 None."""
    text = s.replace(",", "")
    total, last_scale = 0.0, None
    found = False
    for m in _NUM_GROUP_RE.finditer(text):
        scale = _KOR_UNIT.get(m.group(2) or "", 1.0)
        if last_scale is not None and scale >= last_scale:
            return None
        total += float(m.group(1)) * scale
        last_scale = scale
        found = True
    return total if found else None


def _residue_tokens(s: str) -> set[str]:
    """수·단위·수식어를 걷어낸 나머지 어휘 — 통화·단위 충돌('달러' vs '유로') 판별용."""
    text = re.sub(r"[\d.,]+", " ", s)
    out: set[str] = set()
    for token in re.findall(r"[가-힣a-zA-Z%]+", text):
        for word in _RESIDUE_STRIP:
            token = token.replace(word, "")
        if token:
            out.add(token.lower())
    return out


def _same_quantity(value_a: str, value_b: str) -> bool:
    """두 값이 표기만 다른 같은 양인가 — 크기가 같고 단위 어휘가 상충하지 않을 때.

    '90억 달러' vs '90억 유로'는 크기가 같아도 통화가 상충하므로 충돌로 남긴다.
    '520억' vs '520억 달러'(다이제스트의 단위 탈락)는 한쪽이 부분집합이라 동치다.
    """
    ma, mb = _magnitude(value_a), _magnitude(value_b)
    if ma is None or mb is None or abs(ma - mb) > max(abs(ma), abs(mb)) * 1e-9:
        return False
    ra, rb = _residue_tokens(value_a), _residue_tokens(value_b)
    return ra <= rb or rb <= ra


def _value_in_text(value: str, doc_norm: str) -> bool:
    """경고가 인용한 값이 실제 본문(정규화)에 있는가 — 없는 값을 문제 삼으면 창작 경고다.

    수치가 든 값은 숫자 눈금(significant_numbers)으로 재고 — '약 570TWh'처럼 수식어가
    붙어도 570이 있으면 실재다 — 수치 없는 값(시행 중·영문 명칭)은 문자열로 잰다.
    """
    nums = significant_numbers(value)
    if nums:
        return all(normalize_number(n) in doc_norm for n in nums)
    v = _norm_value(value)
    return bool(v) and v in doc_norm


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
        lines.append("선행 챕터에서 이미 인용된 수치(챕터 간 값 충돌 대조용):")
        lines.append(", ".join(prev_digest))
        lines.append("")
    lines.append("검증할 챕터 본문:")
    for plan, content in sections:
        lines.append(f"\n## {plan.chapter_number}.{plan.section_number} {plan.title}\n{content}")
    return "\n".join(lines)[:_MAX_CHAPTER_CHARS]


def _to_rows(
    chapter_number: int,
    manifest: dict[str, Any],
    sid_by_label: dict[str, UUID] | None = None,
) -> list[dict[str, Any]]:
    """모델 JSON → 저장 행. 형식이 어긋난 항목·축 밖 카테고리·충돌 없는 지적은 버린다.

    충돌 판정의 정본은 값 필드 구조다: value_a/value_b가 채워졌고 정규화 후 서로
    다르면 충돌, 같으면 재언급 지적(비결함)이다. 값 필드가 없는 행만 _ASSERT_RE
    어휘 폴백으로 판정한다. 값이 있는 행은 "_values"에 실어 보낸다 — 호출부가
    본문 실증·dedup에 쓰고 저장 전에 걷는다(verify_findings 스키마 밖).

    sid_by_label("2.1" → 절 id)이 있으면 section_ref를 절 안정 id로도 해석해 싣는다 —
    화면의 절 매칭·이동은 id가 정본이고, ref 문자열은 사람이 읽는 표시값이다.
    """
    rows: list[dict[str, Any]] = []
    n_axis, n_assertless, n_same = 0, 0, 0
    samples: list[str] = []
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
        if not any(axis in category_str for axis in _KEPT_AXES):
            n_axis += 1
            samples.append(f"축밖:{detail.strip()[:60]}")
            continue  # 축 밖 — 결정적 검출기·근거 동봉 판정의 영역
        value_a = str(item.get("value_a") or "").strip()
        value_b = str(item.get("value_b") or "").strip()
        if value_a and value_b:
            if _norm_value(value_a) == _norm_value(value_b) or _same_quantity(value_a, value_b):
                n_same += 1
                samples.append(f"동일값:{detail.strip()[:60]}")
                continue  # 두 값이 같으면(표기만 달라도) 충돌이 아니라 재언급이다
        elif not _ASSERT_RE.search(detail):
            n_assertless += 1
            samples.append(f"단정없음:{detail.strip()[:60]}")
            continue  # 값 필드도 충돌 단정도 없는 '확인 필요'류는 경고가 아니다
        section_ref = section.strip()[:20] if isinstance(section, str) and section.strip() else None
        section_id = None
        if section_ref and sid_by_label:
            # LLM이 "2.1 표 2-1"처럼 덧붙이기도 한다 — 앞의 번호만 해석한다.
            section_id = sid_by_label.get(section_ref.split()[0])
        row: dict[str, Any] = {
            "chapter_number": chapter_number,
            "severity": severity if severity in _SEVERITIES else "warning",
            "category": category_str[:40],
            "section_ref": section_ref,
            "section_id": section_id,
            "detail": detail.strip(),
        }
        if value_a and value_b:
            row["_values"] = [value_a, value_b]
        rows.append(row)
        if len(rows) >= MAX_FINDINGS_PER_CHAPTER:
            break
    if n_axis or n_assertless or n_same:
        # 걸러낸 것을 조용히 버리지 않는다 — 필터가 진짜 신호를 먹는지 셀 수 있게
        # 표본까지 남긴다(다보고서 회귀 때 이 로그가 미탐 감사 자료다).
        logger.info(
            "pm_verify.rows_filtered",
            chapter=chapter_number,
            dropped_axis=n_axis,
            dropped_assertless=n_assertless,
            dropped_same_value=n_same,
            kept=len(rows),
            samples=samples[:4],
        )
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
    seen_texts: list[str] = []  # 선행 챕터 본문 누적 — 값 충돌 대조 다이제스트 원천
    seen_pairs: set[frozenset[str]] = set()  # 이미 경고한 값 조합 — 교차 챕터 dedup
    n_dup, n_ghost = 0, 0
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
        sid_by_label = {
            f"{plan.chapter_number}.{plan.section_number}": plan.section_id
            for plan, _content in sections
        }
        # 지금까지의 문서 전체(선행 챕터+현재 챕터, 정규화) — 값 실증의 눈금.
        # 모델은 현재 챕터+선행 다이제스트만 보므로 이 범위 밖의 값은 인용할 수 없다.
        doc_norm = _norm_value(
            "\n".join(seen_texts) + "\n" + "\n".join(content for _, content in sections)
        )
        for row in _to_rows(chapter_number, _parse_manifest(response.content), sid_by_label):
            values = row.pop("_values", [])
            # 값 실증 — 경고가 인용한 값이 본문 어디에도 없으면 그 경고 자체가 창작이다.
            if values and not all(_value_in_text(v, doc_norm) for v in values):
                n_ghost += 1
                logger.info(
                    "pm_verify.ghost_value_dropped",
                    chapter=chapter_number,
                    detail=row["detail"][:80],
                )
                continue
            # 같은 값 조합의 충돌을 챕터마다 다시 내면(선행 다이제스트가 원인) 화면에
            # 세 번 실린다 — 값 조합이 겹치는 경고는 처음 것만 남긴다. 값 필드가 있으면
            # 그것이 키(용어 혼용도 dedup), 없으면 detail의 수치 조합 폴백.
            key = (
                frozenset(_norm_value(v) for v in values)
                if len(values) >= 2
                else frozenset(normalize_number(t) for t in significant_numbers(row["detail"]))
            )
            if len(key) >= 2 and key in seen_pairs:
                n_dup += 1
                continue
            if len(key) >= 2:
                seen_pairs.add(key)
            rows.append(row)
        seen_texts.extend(content for _, content in sections)
    if n_dup or n_ghost:
        logger.info(
            "pm_verify.rows_deduped",
            project_id=str(state.project_id),
            n_dup=n_dup,
            n_ghost=n_ghost,
        )
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
        section_rows = (
            await session.execute(
                select(
                    Section.id, Section.chapter_number, Section.section_number, Section.meta
                ).where(Section.project_id == project_id, Section.content != "")
            )
        ).all()
    sid_by_label = {f"{r.chapter_number}.{r.section_number}": r.id for r in section_rows}
    entries = [e for r in section_rows for e in (r.meta or {}).get("ledger_entries") or []]
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
                "section_id": sid_by_label.get(str(f["section_ref"])),
                "detail": f["detail"],
            }
        )
    if out:
        logger.info("pm_verify.ledger_conflicts", project_id=str(project_id), n=len(out))
    return out


async def persist_findings(project_id: UUID, rows: list[dict[str, Any]]) -> None:
    """프로젝트 단위 전량 교체 저장 — 재실행 시 stale 경고가 남지 않는다.

    저장과 **같은 트랜잭션에서** 판정 대상 본문의 지문·시각을 찍는다(projects.config
    의 _verify_stamp). 이게 없으면 화면은 "지금 남아 있는 경고가 지금 본문에 대한
    판정인지"를 알 방법이 없다 — 절을 고쳐도 검증은 자동으로 다시 돌지 않으므로,
    낡은 경고가 최신 판정처럼 보였다(2026-08-27). 지문은 버전 스냅샷과 같은 것을 써서
    두 기능이 "본문이 달라졌다"를 같은 기준으로 말하게 한다.
    """
    from src.db.models.project import Project
    from src.db.models.verify_finding import VerifyFinding
    from src.db.session import async_session_maker
    from src.services.sections.versions import sections_fingerprint

    async with async_session_maker() as session:
        await session.execute(delete(VerifyFinding).where(VerifyFinding.project_id == project_id))
        for row in rows:
            session.add(VerifyFinding(project_id=project_id, **row))
        project = await session.get(Project, project_id)
        if project is not None:
            stamp = {
                "hash": await sections_fingerprint(session, project_id),
                "at": clock_now().isoformat(),
            }
            project.config = {**(project.config or {}), "_verify_stamp": stamp}
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
    try:
        # 하위 헤딩 번호 검사(결정적) — v5c-2 정독 실측 결함 계급(결번·고아·유령 참조·
        # 제목 재출력). 형식 결함이라 warning으로만 표면화한다.
        from src.services.qa.heading_check import heading_findings

        pairs = [(plan, content) for _, secs in _group_by_chapter(state) for plan, content in secs]
        rows.extend(heading_findings(pairs))
    except Exception:
        logger.warning("pm_verify.heading_failed", project_id=str(state.project_id), exc_info=True)
    try:
        # 키포인트 미반영(결정적, 웹 전용) — 자료 보강/재작성 신호(사용자 결정 8/20).
        from src.services.qa.keypoints import keypoint_findings

        triples = [(plan, content, list(plan.key_points or [])) for plan, content in pairs]
        rows.extend(keypoint_findings(triples))
    except Exception:
        logger.warning(
            "pm_verify.keypoints_failed", project_id=str(state.project_id), exc_info=True
        )
    await persist_findings(state.project_id, rows)
    n_critical = sum(1 for r in rows if r["severity"] == "critical")
    logger.info(
        "pm_verify.done",
        project_id=str(state.project_id),
        n_findings=len(rows),
        n_critical=n_critical,
    )
    return len(rows)
