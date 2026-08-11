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

# 비표준 인용 마커 — 대괄호 표기 중 정상 [n]이 아닌 것(2026-08-05 실측:
# '[배경자료 제공됨]', '[배경 맥락]', '[근거 없음 - …]' 등 모델이 발명한 표기).
# 마크다운 링크 [텍스트](url)와 그림/표 캡션([그림 1-1])은 정상 표기라 제외한다.
_CITE_MARKER_RE = re.compile(r"\[\d+\]")
_BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,40})\](?!\()")
_ALLOWED_BRACKET_RE = re.compile(r"^(?:\d+|그림\s?[\d\-. ]+|표\s?[\d\-. ]+)$")

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


def ungrounded_numbers(content: str, cited_content: str) -> list[str]:
    """본문에서 인용 근거에 없는 유의미한 숫자 토큰(등장 순서·중복 제거).

    게이트(생성 시점)와 편집 화면(조회 시점)이 같은 판정을 쓰도록 분리했다 —
    화면에서 "이 절의 근거 미확인 수치"를 그대로 보여주기 위함(2026-08-09).
    퍼지 매칭(콤마 정규화 후 부분문자열)이라 오탐 여지가 있어 경고용이다.
    """
    haystack = cited_content.replace(",", "")
    out: list[str] = []
    seen: set[str] = set()
    for token in _significant_numbers(content):
        norm = _normalize_number(token)
        if norm in seen:
            continue
        seen.add(norm)
        if norm and norm not in haystack:
            out.append(token)
    return out


def check_numeric_grounded(draft: SectionDraft, cited_content: str) -> GateResult:
    """본문의 유의미한 숫자가 인용된 근거 청크 본문에 실제로 등장하는지.

    퍼지 매칭(콤마 정규화 후 부분문자열)이라 오탐 여지가 있어 SOFT — 미매칭
    숫자는 제외 사유가 아니라 사람에게 넘기는 '확인 요망' 플래그다.
    """
    ungrounded = ungrounded_numbers(draft.content, cited_content)
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


# 문장 분리 — 마침표·물음표·느낌표 뒤 공백. 개조식은 줄 자체가 한 단위라 줄 먼저 나눈다.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# 본문이 아닌 줄: 제목·표·인용블록·구분선.
_NON_CLAIM_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s|\||>|[-*_]{3,}\s*$)")
# 글머리 기호(개조식) — 판정에서 떼어내고 길이를 잰다. 'ㅇ'·'ㅁ'은 한글 자모 마커다.
_BULLET_RE = re.compile(r"^\s*(?:[-*•‣◦○□▪ㅇㅁ]|\d+[.)]|[가-힣][.)])\s+")
# 주장으로 볼 최소 길이 — 이보다 짧은 줄은 소제목·나열 항목이라 인용을 요구하지 않는다.
_MIN_CLAIM_CHARS = 25
# 서술을 끝맺는 꼬리 — 개조식 소제목("…확보 전략 제언")과 주장("…취약성에서 발생하고 있음")을
# 가르는 실측 기준(2026-08-11, 예타 6.2절). 제목은 명사로 끝나고 주장은 종결형으로 끝난다.
# 이 검사는 사람에게 보내는 경고라 재현율보다 정밀도가 중요하다 — 애매하면 세지 않는다.
_CLAIM_TAILS: tuple[str, ...] = (
    "다",
    "음",
    "임",
    "함",
    "됨",
    "짐",
    "필요",
    "전망",
    "예상",
    "우려",
    "가능",
    "요구",
    "시급",
    "중요",
)
_SENTENCE_END_RE = re.compile(r"[.!?]\s*$")


def claim_units(content: str) -> list[str]:
    """본문을 '주장 단위'(문장·개조식 항목)로 자른다 — 인용 여부와 무관하게 전부.

    제목·표·구분선과 짧은 나열 항목, 명사로 끝나는 소제목은 주장이 아니라 제외한다.
    근거 추적(services/qa/alignment)과 미인용 검사가 같은 단위를 봐야 화면의 숫자와
    경고가 어긋나지 않는다 — 그래서 분해를 여기 하나로 둔다.
    """
    out: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or _NON_CLAIM_LINE_RE.match(line):
            continue
        body = _BULLET_RE.sub("", line)
        for unit in _SENTENCE_SPLIT_RE.split(body):
            unit = unit.strip()
            # 판정은 마커를 뗀 문장으로 한다 — 개조식은 "…성장했음 [3]"처럼 마커로
            # 끝나는 줄이 대부분이라, 원문 그대로 보면 종결형 검사에서 전부 탈락한다
            # (2026-08-11 실측: 인용 340개짜리 절의 주장이 0건으로 잡혔다).
            bare = _CITE_MARKER_RE.sub("", unit).strip()
            if len(bare) < _MIN_CLAIM_CHARS:
                continue
            if not _SENTENCE_END_RE.search(bare) and not bare.endswith(_CLAIM_TAILS):
                continue  # 명사로 끝나면 소제목 — 인용을 요구하지 않는다
            out.append(unit)
    return out


def uncited_units(content: str) -> list[str]:
    """인용 마커가 없는 주장 단위 목록.

    게이트와 화면이 같은 판정을 쓰도록 분리했다(ungrounded_numbers와 같은 규약).
    그것까지 세면 개조식 보고서는 항상 절반이 '미인용'으로 나와 신호가 죽는다.
    """
    return [u for u in claim_units(content) if not _CITE_MARKER_RE.search(u)]


def check_uncited_claims(draft: SectionDraft) -> GateResult:
    """근거 마커가 붙지 않은 주장이 지나치게 많은지.

    마커가 가리키는 청크와 본문이 어긋나는 것은 numeric_grounded가 잡지만, 아예
    마커가 없는 문장은 어떤 검사에도 안 걸려 그대로 통과했다. 모델이 근거 없이
    쓴 대목이 여기서 드러난다. 개조식 특성상 이어지는 항목은 마커를 생략하는 게
    자연스러워 SOFT — 비율이 절반을 넘을 때만 사람에게 알린다.
    """
    units = uncited_units(draft.content)
    total = len(units) + len(_CITE_MARKER_RE.findall(draft.content))
    ratio = len(units) / total if total else 0.0
    passed = len(units) < 3 or ratio <= 0.5
    detail = None
    if not passed:
        preview = " / ".join(u[:30] for u in units[:2])
        detail = f"근거 표기 없는 주장 {len(units)}건({ratio:.0%}): {preview}"
    return GateResult(
        check="uncited_claims",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=detail,
    )


def check_citation_markers(draft: SectionDraft) -> GateResult:
    """인용 표기가 표준([n])인지 — 모델이 발명한 '[배경자료 제공됨]'류 마커 검출.

    배경 요약(인용 불가)을 근거 삼을 때 나오는 오염 신호라 SOFT 경고로 사람에게
    보여준다(후보 제외는 아님 — 본문 자체는 유효할 수 있다).
    """
    found: list[str] = []
    for m in _BRACKET_RE.finditer(draft.content):
        inner = m.group(1).strip()
        if not _ALLOWED_BRACKET_RE.match(inner) and inner not in found:
            found.append(inner)
    passed = not found
    detail = None
    if not passed:
        preview = ", ".join(f"[{t}]" for t in found[:3])
        detail = f"비표준 인용 마커 {len(found)}종: {preview} - 인용은 [숫자]만 허용"
    return GateResult(
        check="citation_markers",
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
        check_citation_markers(draft),
        check_numeric_grounded(draft, cited_content),
        check_uncited_claims(draft),
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
