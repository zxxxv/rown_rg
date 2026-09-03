"""저장 직전 결정층 — 인용 마커 자동 교정(3상태) + 절 내 중복 소거.

철강 정독·일원화 실측(2026-08-29~31)의 처방을 검출에서 교정으로 승격한 것.
26쌍 전수 판정에서 얻은 설계 제약(2026-09-01 사용자 검토 확정)을 그대로 따른다:

① 3상태 — 교정 / 유지 / 보류. "부재하면 아무 청크나 붙이는" 이진 판정 금지.
   - 교정: 인용 청크에 없는 수치가 팩의 **한 청크에 전부** 실재할 때만 그 청크로.
   - 유지: 실재·양쪽 실재·파생값(합산 등)·외국어 인용 근거(어휘로 '없다' 선언 불가).
   - 보류: 팩 어디에도 없음 — 파생 아니면 환각 후보다. 마커를 손대지 않고 목록으로
     넘긴다(무근거 검출기·사람 몫). 억지 교정은 경고만 하던 종전보다 나쁘다.
② 치환은 주장 단위(문장·불릿) 안의 마커 문자열만 — 줄 단위 치환은 표 이웃 셀의
   출처까지 덮는다(2026-08-31 실사고).
③ 마커를 바꾸거나 문단을 지우면 cited_chunk_ids를 **반드시 재규약**한다
   (본문 첫 등장 순서 ↔ cited 목록, renumber._local_to_global 규약). 안 하면
   조립 재번호에서 절 전체 인용이 밀린다(v6 renumber 사고의 재연 경로).

수치 대조는 무근거 검출기와 같은 자(gate.numeric_mentions/number_in_text/
derived_numbers)를 쓴다 — 교정과 검출이 다른 눈금이면 서로를 못 믿는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

import structlog

from src.core.citations import MARK_RE, numbers_in_order
from src.services.qa.alignment import _weighted_tokens, token_similarity
from src.services.qa.cross_section import (
    _POINTER_TAIL_RE,
    DUPLICATE_THRESHOLD,
    MIN_UNIT_CHARS,
)
from src.services.qa.gate import (
    claim_units,
    derived_numbers,
    normalize_haystack,
    normalize_number,
    number_in_text,
    numeric_mentions,
)

logger = structlog.get_logger(__name__)

# 교정 후보 탐색에서 문장당 대조할 수치 상한 — 상위 수치가 일치하면 충분하다.
_MAX_TOKENS_PER_UNIT = 3
# 절 내 중복 판정 임계 = 절 간 검출(cross_section)과 같은 경계여야 검출과 수리가
# 같은 것을 본다 - 값 복사이던 것을 import로(2026-09-03 통일).
_INTRA_DUP_THRESHOLD = DUPLICATE_THRESHOLD
# 절 내 소거 가드레일 — 이 비율 넘게 얇아지면 중단한다(제거 방향 처방은 보고서를
# 조용히 얇게 만든다는 검토 지적 반영). 남는 건 검출기 경고 몫.
_MAX_REMOVAL_RATIO = 0.15
_MIN_UNIT_CHARS = MIN_UNIT_CHARS
_HANGUL_RE = re.compile(r"[가-힣]")  # gate._HANGUL_RE와 같은 판정(비공개라 로컬 정의)
# 출처형 마커 하나 — 교정 치환용(직접 인용 [n]은 원문 전사라 교정 대상이 아니다).
_ONE_SOURCE_MARK_RE = re.compile(r"\(출처\s*\d+(?:\s*,\s*\d+)*\s*\)")
# 술어가 '…절 참조'인 위임 문장 — 중복 소거 대상이 아니다(cross_section과 같은 판정
# - 정규식도 그쪽 정의를 그대로 쓴다, 2026-09-03 통일).
_POINTER_RE = _POINTER_TAIL_RE
# 개조식 마커 깊이 — 삭제할 줄보다 깊은 하위 불릿이 바로 이어지면 고아가 되므로 포기.
_MARKER_DEPTH = {"□": 0, "ㅇ": 1, "○": 1, "◦": 1, "-": 2, "*": 3}
_LINE_MARKER_RE = re.compile(r"^\s*([□ㅇ○◦*-])\s+")
# 지워진 문장을 가리킬 지시어 — 다음 줄이 이걸로 시작하면 흐름이 끊기므로 포기.
_ANAPHORA_RE = re.compile(r"^(이는|이러한|이에|이를|그\s*결과|따라서|또한|아울러|즉)")


@dataclass
class RepairOutcome:
    """교정·소거 결과 — content/cited가 함께 움직인다(따로 쓰면 규약이 깨진다).

    removed/held/budget_exhausted는 감사 기록이다(2026-09-01 검토: "결정적이면
    조용히 틀린다" — 지운 것이 사람 눈에 걸릴 경로가 있어야 임계 오류를 발견한다).
    절 meta로 실려 검증 카드(층4)가 노출한다.
    """

    content: str
    cited_chunk_ids: list[UUID]
    n_fixed: int = 0
    n_removed: int = 0
    held: list[str] = field(default_factory=list)  # 보류 수치(팩 어디에도 없음)
    # 삭제 감사 기록 — {kept: 남은 문장, removed: 지운 문장, score: 유사도}
    removed: list[dict[str, object]] = field(default_factory=list)
    fixed: list[dict[str, object]] = field(default_factory=list)  # 교정 전/후 마커
    budget_exhausted: bool = False  # 예산 소진 = 병리 심한 절 신호(성공 아님, 경보)

    @property
    def changed(self) -> bool:
        return bool(self.n_fixed or self.n_removed)

    def audit(self) -> dict[str, object]:
        """절 meta에 실을 감사 요약 — 층4(검증 카드)가 읽는다."""
        out: dict[str, object] = {}
        if self.removed:
            out["removed"] = self.removed[:12]
        if self.fixed:
            out["fixed"] = self.fixed[:12]
        if self.held:
            out["held"] = self.held[:12]
        if self.budget_exhausted:
            out["budget_exhausted"] = True
        return out


def _local_chunk_map(content: str, cited: list[UUID]) -> dict[int, UUID]:
    """로컬 번호 → 청크. 첫 등장 i번째 고유 번호 ↔ cited[i] 규약을 편다."""
    out: dict[int, UUID] = {}
    for i, n in enumerate(numbers_in_order(content)):
        if i >= len(cited):
            break
        out.setdefault(n, cited[i])
    return out


def _rebuild_cited(content: str, chunk_of: dict[int, UUID]) -> list[UUID]:
    """본문 첫 등장 순서로 cited_chunk_ids를 다시 편다 — 규약의 재수립."""
    return [chunk_of[n] for n in numbers_in_order(content) if n in chunk_of]


def _has_ghost_numbers(content: str, chunk_of: dict[int, UUID]) -> bool:
    """규약 밖 번호(마커 수 > cited 길이)가 있으면 참 — 재규약이 자리 배정을 어긋나게
    할 수 있어 이 절은 손대지 않는다(유령 출처 경고가 따로 잡는 기형 초안)."""
    return any(n not in chunk_of for n in numbers_in_order(content))


def repair_citations(
    content: str,
    cited_chunk_ids: list[UUID],
    pool: dict[UUID, str],
) -> RepairOutcome:
    """수치 문장의 마커를 인용 청크 실재 여부로 대조해 3상태로 처리한다."""
    chunk_of = _local_chunk_map(content, cited_chunk_ids)
    if not chunk_of or not pool or _has_ghost_numbers(content, chunk_of):
        return RepairOutcome(content, list(cited_chunk_ids))
    norm_pool = {cid: normalize_haystack(text or "") for cid, text in pool.items()}
    local_of_chunk: dict[UUID, int] = {}
    for n, cid in chunk_of.items():
        local_of_chunk.setdefault(cid, n)
    next_no = max(chunk_of) + 1

    out = content
    fixed: list[dict[str, object]] = []
    held: list[str] = []
    for unit in claim_units(content):
        marks = list(dict.fromkeys(numbers_in_order(unit)))
        if not marks:
            continue
        # 직접 인용 [n]이 섞였거나 마커가 여럿이면 손대지 않는다 — 어느 마커가 그
        # 수치의 것인지 결정할 수 없다(정밀도 우선, 검출기 경고 몫).
        source_marks = _ONE_SOURCE_MARK_RE.findall(unit)
        if len(marks) != 1 or len(source_marks) != 1:
            continue
        cited_id = chunk_of.get(marks[0])
        if cited_id is None:
            continue
        cited_text = norm_pool.get(cited_id, "")
        # 외국어 인용 근거엔 어휘로 '없다'를 선언할 수 없다 — 오귀속 검출기와 같은 원칙.
        if cited_text.strip() and not _HANGUL_RE.search(cited_text):
            continue
        bare = MARK_RE.sub(" ", unit)
        derived = derived_numbers(bare)
        missing = []
        for tok in numeric_mentions(bare)[:_MAX_TOKENS_PER_UNIT]:
            norm = normalize_number(tok)
            if not norm or norm in derived:
                continue
            if not number_in_text(tok, cited_text):
                missing.append(tok)
        if not missing:
            continue  # 유지 — 인용 청크에 실재
        # 교정 대상 탐색: 빠진 수치 **전부**를 가진 단일 청크만 인정한다.
        target: UUID | None = None
        for cid, text in norm_pool.items():
            if cid == cited_id:
                continue
            if all(number_in_text(tok, text) for tok in missing):
                target = cid
                break
        if target is None:
            held.extend(missing)  # 보류 — 팩 어디에도 없음(파생 확장형이거나 환각 후보)
            continue
        local = local_of_chunk.get(target)
        if local is None:
            local = next_no
            next_no += 1
            local_of_chunk[target] = local
            chunk_of[local] = target
        new_unit = _ONE_SOURCE_MARK_RE.sub(f"(출처 {local})", unit, count=1)
        if new_unit != unit and unit in out:
            out = out.replace(unit, new_unit, 1)
            fixed.append({"unit": unit[:80], "from": source_marks[0], "to": f"(출처 {local})"})

    rebuilt = _rebuild_cited(out, chunk_of) if fixed else list(cited_chunk_ids)
    if fixed or held:
        logger.info(
            "citation_repair.done",
            n_fixed=len(fixed),
            n_held=len(held),
            held_samples=held[:3],
        )
    return RepairOutcome(out, rebuilt, n_fixed=len(fixed), held=held, fixed=fixed)


def dedup_intra_section(content: str, cited_chunk_ids: list[UUID]) -> RepairOutcome:
    """절 안에서 복사 수준(0.8+)으로 겹치는 뒤 문장을 결정적으로 걷어낸다.

    파트 분할 작성이 같은 소재를 두 파트에서 각각 소화한 흔적(철강 1.2 실측: 같은
    문단 두 벌). 절 간 중복(cross_section)과 같은 자로 재되, '같은 절 안은 안 본다'
    제약만 푼 것. 표·헤딩은 claim_units가 이미 빼고, 위임 포인터·리드(첫 유닛)는
    남긴다. 가드레일: 전체의 15% 넘게 얇아지면 중단 — 나머지는 검출기 경고 몫.
    """
    units = [
        u
        for u in claim_units(content)
        if len(u) >= _MIN_UNIT_CHARS and not _POINTER_RE.search(MARK_RE.sub("", u).strip())
    ]
    if len(units) < 2:
        return RepairOutcome(content, list(cited_chunk_ids))
    chunk_of = _local_chunk_map(content, cited_chunk_ids)
    if _has_ghost_numbers(content, chunk_of):
        return RepairOutcome(content, list(cited_chunk_ids))
    tokens = [_weighted_tokens(MARK_RE.sub(" ", u)) for u in units]
    numsets = [{normalize_number(t) for t in numeric_mentions(MARK_RE.sub(" ", u))} for u in units]

    out = content
    removed_chars = 0
    records: list[dict[str, object]] = []
    budget = int(len(content) * _MAX_REMOVAL_RATIO)
    budget_hit = False
    kept: list[int] = []
    for j in range(len(units)):
        dup_of = next(
            (
                i
                for i in kept
                # 숫자 집합이 다르면 삭제 금지 — 골격만 같은 연도별 추이 문장
                # ("2024년 3.2조" vs "2025년 4.1조")은 중복이 아니라 정보다
                # (2026-09-01 검토). 둘 다 무수치면 순수 문장 중복으로 본다.
                if numsets[i] == numsets[j]
                and token_similarity(tokens[i], tokens[j]) >= _INTRA_DUP_THRESHOLD
            ),
            None,
        )
        if dup_of is None:
            kept.append(j)
            continue
        unit = units[j]
        # 첫 중복 1건은 항상 걷는다 — 그게 이 검사의 표적 계급이다. 이후부터 예산
        # 가드레일(15%)이 대량 소거를 막는다. 단 예산 소진은 성공이 아니라 경보다 —
        # 병리가 가장 심한 절일수록 방어가 약해진다는 뜻이라 플래그로 층4에 올린다.
        if records and removed_chars + len(unit) > budget:
            budget_hit = True
            kept.append(j)
            continue
        shrunk = _remove_unit_line(out, unit)
        if shrunk is None:
            kept.append(j)  # 줄 일부·고아 하위·지시어 후속 — 어색해질 자리라 포기
            continue
        out = shrunk
        removed_chars += len(unit)
        records.append(
            {
                "kept": units[dup_of][:80],
                "removed": unit[:80],
                "score": round(token_similarity(tokens[dup_of], tokens[j]), 3),
            }
        )

    rebuilt = _rebuild_cited(out, chunk_of) if records else list(cited_chunk_ids)
    if records or budget_hit:
        logger.info(
            "intra_dedup.removed",
            n_removed=len(records),
            removed_chars=removed_chars,
            budget_exhausted=budget_hit,
            samples=[r["removed"] for r in records[:3]],
        )
    return RepairOutcome(
        out, rebuilt, n_removed=len(records), removed=records, budget_exhausted=budget_hit
    )


def _line_depth(line: str) -> int | None:
    m = _LINE_MARKER_RE.match(line)
    return _MARKER_DEPTH.get(m.group(1)) if m else None


def _remove_unit_line(content: str, unit: str) -> str | None:
    """unit이 통째로 이루는 줄 하나를 걷는다. 어색해질 자리면 None(포기).

    포기 조건(2026-09-01 검토 — "코드로 지우면 어색해진다"의 결정적 가드):
    ① 문장이 줄의 일부다 — 도려내면 조각이 남는다
    ② 다음 실줄이 더 깊은 불릿이다 — 부모를 지우면 고아가 된다
    ③ 다음 실줄이 지시어로 시작한다 — 지워진 문장을 가리켜 흐름이 끊긴다
    """
    # 같은 문장이 여럿이면 **뒤 등장**을 걷는다 — 남기는 쪽은 첫 등장이다(중복 판정과
    # 같은 방향). find로 첫 등장을 지우면 남길 줄을 지우고 가드도 엉뚱한 이웃을 본다.
    idx = content.rfind(unit)
    if idx < 0:
        return None
    ls = content.rfind(chr(10), 0, idx) + 1
    le = content.find(chr(10), idx)
    le = len(content) if le < 0 else le
    line = content[ls:le]
    if _LINE_MARKER_RE.sub("", line).strip() != unit.strip():
        return None  # ① 줄 일부
    depth = _line_depth(line)
    rest = content[le + 1 :] if le < len(content) else ""
    next_line = next((ln for ln in rest.split(chr(10)) if ln.strip()), "")
    next_depth = _line_depth(next_line)
    if depth is not None and next_depth is not None and next_depth > depth:
        return None  # ② 고아 하위 불릿
    if _ANAPHORA_RE.match(_LINE_MARKER_RE.sub("", next_line).strip()):
        return None  # ③ 지시어 후속
    cut_end = le + 1 if le < len(content) else le
    return content[:ls] + content[cut_end:]


def post_process_draft(
    content: str, cited_chunk_ids: list[UUID], pool: dict[UUID, str]
) -> RepairOutcome:
    """저장 직전 결정층 한 묶음 — 마커 교정 → 절 내 소거 순서(교정이 먼저라야
    소거로 지워질 문장에 헛교정을 안 한다는 보장은 없지만, 소거 후 재규약이 교정의
    번호 배정을 무효화하지 않도록 한 방향으로 고정한다)."""
    repaired = repair_citations(content, cited_chunk_ids, pool)
    deduped = dedup_intra_section(repaired.content, repaired.cited_chunk_ids)
    return RepairOutcome(
        deduped.content,
        deduped.cited_chunk_ids,
        n_fixed=repaired.n_fixed,
        n_removed=deduped.n_removed,
        held=repaired.held,
        fixed=repaired.fixed,
        removed=deduped.removed,
        budget_exhausted=deduped.budget_exhausted,
    )
