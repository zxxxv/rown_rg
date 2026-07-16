"""보고서 후보의 정적 게이트 — 순수 결정적 검사 (LLM·DB 없음).

설계 불변식: AI는 후보를 생성만 하고, 합격/불합격은 여기의 코드가 결정한다.
LLM-judge를 쓰지 않으므로 무한 루프·비결정성이 원천 차단된다. 각 검사는
GateResult(check, severity, passed, detail)를 돌려주고, HARD 실패는 후보를
제외(사람에게 안 보임), SOFT 실패는 경고로 사람에게 표시된다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from uuid import UUID

from src.core.types import (
    CheckSeverity,
    GateResult,
    RetrievedChunk,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    StaticCheckReport,
)

# 기본 길이 경계 (문자 수) — 필요하면 호출 시 오버라이드.
DEFAULT_MIN_CHARS = 200
DEFAULT_MAX_CHARS = 4000

# 미완성 초안에서 흔한 잔여 placeholder 토큰.
_PLACEHOLDER_TOKENS: tuple[str, ...] = ("{{", "}}", "[[", "]]", "TODO", "TBD", "XXX", "<채워넣기>")

# 숫자·비율 토큰: 앞자리 숫자 + 선택적 천단위 콤마 + 선택적 소수부 + 선택적 %.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

# 제어문자 (렌더 불가 신호) — 탭·개행·캐리지리턴(\x09-\x0d 중 일부)은 허용.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def check_citation_resolves(draft: SectionDraft, valid_chunk_ids: set[UUID]) -> GateResult:
    """draft가 인용한 chunk_id가 전부 근거 풀(검색 결과)에 실재하는지. (hallucinated 인용 차단)"""
    unresolved = [cid for cid in draft.cited_chunk_ids if cid not in valid_chunk_ids]
    passed = not unresolved
    detail = None
    if not passed:
        preview = ", ".join(str(c) for c in unresolved[:3])
        detail = f"근거 풀에 없는 인용 {len(unresolved)}건: {preview}"
    return GateResult(
        check="citation_resolves",
        severity=CheckSeverity.HARD,
        passed=passed,
        detail=detail,
    )


def check_renderable(draft: SectionDraft) -> GateResult:
    """본문이 렌더 가능한지 — 비어있지 않고 제어문자가 없어야.

    지금은 텍스트 sanity만 검사한다. HWPX 직렬화가 준비되면 여기서 실제 직렬화
    성공 여부를 검사하도록 확장한다(TODO: HWPX serialize seam).
    """
    if not draft.content.strip():
        return GateResult(
            check="renderable",
            severity=CheckSeverity.HARD,
            passed=False,
            detail="본문이 비어 있음",
        )
    ctrl = _CONTROL_RE.search(draft.content)
    if ctrl is not None:
        return GateResult(
            check="renderable",
            severity=CheckSeverity.HARD,
            passed=False,
            detail=f"렌더 불가 제어문자 포함 (U+{ord(ctrl.group()):04X})",
        )
    return GateResult(check="renderable", severity=CheckSeverity.HARD, passed=True)


def _normalize_number(token: str) -> str:
    """콤마 제거 + 후행 % 제거 — 매칭용 정규화."""
    return token.replace(",", "").rstrip("%")


def _significant_numbers(text: str) -> list[str]:
    """본문에서 '사실 주장'으로 볼 만한 숫자만 추출.

    구조적 소수(1개·2장 등 한 자리)는 오탐이 많아 건너뛰고, 두 자리 이상이거나
    소수/퍼센트를 포함한 토큰만 검사 대상으로 삼는다.
    """
    out: list[str] = []
    for m in _NUMBER_RE.finditer(text):
        token = m.group()
        digits = _normalize_number(token).replace(".", "")
        if "." in token or "%" in token or len(digits) >= 2:
            out.append(token)
    return out


def check_numeric_grounded(draft: SectionDraft, cited_content: str) -> GateResult:
    """본문의 유의미한 숫자가 인용된 근거 청크 본문에 실제로 등장하는지.

    퍼지 매칭(콤마 정규화 후 부분문자열)이라 오탐 여지가 있어 SOFT — 미매칭
    숫자는 제외 사유가 아니라 사람에게 넘기는 '확인 요망' 플래그다.
    """
    haystack = cited_content.replace(",", "")
    ungrounded: list[str] = []
    seen: set[str] = set()
    for token in _significant_numbers(draft.content):
        norm = _normalize_number(token)
        if norm in seen:
            continue
        seen.add(norm)
        if norm and norm not in haystack:
            ungrounded.append(token)
    passed = not ungrounded
    detail = None
    if not passed:
        preview = ", ".join(ungrounded[:5])
        detail = f"근거에서 확인 안 되는 숫자 {len(ungrounded)}건: {preview}"
    return GateResult(
        check="numeric_grounded",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=detail,
    )


def check_bounds(
    draft: SectionDraft,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    forbidden_terms: Sequence[str] = (),
) -> GateResult:
    """길이 경계·금칙어·잔여 placeholder 검사."""
    content = draft.content
    length = len(content.strip())
    problems: list[str] = []
    if length < min_chars:
        problems.append(f"너무 짧음 ({length}자 < {min_chars})")
    if length > max_chars:
        problems.append(f"너무 김 ({length}자 > {max_chars})")
    found_placeholders = [tok for tok in _PLACEHOLDER_TOKENS if tok in content]
    if found_placeholders:
        problems.append(f"잔여 placeholder: {', '.join(found_placeholders)}")
    found_forbidden = [t for t in forbidden_terms if t and t in content]
    if found_forbidden:
        problems.append(f"금칙어: {', '.join(found_forbidden)}")
    passed = not problems
    return GateResult(
        check="bounds",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=None if passed else "; ".join(problems),
    )


def run_section_gate(
    draft: SectionDraft,
    chunks: Sequence[RetrievedChunk],
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    forbidden_terms: Sequence[str] = (),
) -> StaticCheckReport:
    """한 섹션 후보에 대해 per-candidate 검사를 모두 돌려 종합 리포트 생성."""
    valid_ids = {c.chunk_id for c in chunks}
    cited_ids = set(draft.cited_chunk_ids)
    cited_content = "\n".join(c.content for c in chunks if c.chunk_id in cited_ids)
    results = [
        check_citation_resolves(draft, valid_ids),
        check_renderable(draft),
        check_numeric_grounded(draft, cited_content),
        check_bounds(
            draft,
            min_chars=min_chars,
            max_chars=max_chars,
            forbidden_terms=forbidden_terms,
        ),
    ]
    return StaticCheckReport(results=results)


def gate_candidates(
    section_id: UUID,
    drafts: Sequence[SectionDraft],
    chunks: Sequence[RetrievedChunk],
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    forbidden_terms: Sequence[str] = (),
) -> SectionCandidateSet:
    """섹션 후보 draft들을 검사해 SectionCandidateSet으로 묶는다."""
    candidates = [
        SectionCandidate(
            draft=d,
            report=run_section_gate(
                d,
                chunks,
                min_chars=min_chars,
                max_chars=max_chars,
                forbidden_terms=forbidden_terms,
            ),
        )
        for d in drafts
    ]
    return SectionCandidateSet(section_id=section_id, candidates=candidates)


def check_structure_complete(
    selected: Sequence[SectionDraft],
    plan: Sequence[SectionPlan],
) -> GateResult:
    """조립 후 보고서 레벨 검사 — 선택된 초안이 계획된 전 섹션을 빠짐없이 덮는지."""
    planned = {s.section_id for s in plan}
    drafted = {d.section_id for d in selected}
    missing = planned - drafted
    passed = not missing
    detail = None if passed else f"누락 섹션 {len(missing)}개"
    return GateResult(
        check="structure_complete",
        severity=CheckSeverity.HARD,
        passed=passed,
        detail=detail,
    )
