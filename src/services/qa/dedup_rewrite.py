"""3층 국소 재작성 — 절 간 중복 문단을 참조 전환으로 압축(로드맵 3층, 2026-08-20).

소유권(계획)·사슬(생성)이 예방하고 남은 중복을 조립 시점에 기계로 걷어낸다.
검출은 PM 경고와 **같은 검출기**(cross_section.duplicate_pairs 0.80) — 검출기와
처치가 다르면 고친 것과 경고가 어긋난다(이중 투자 금지 원칙).

처치 원칙(정독·외부 실측 반영):
- **후행 절의 해당 문단만** 건드린다 — 전체 수정은 정상 내용을 16~27% 퇴행시킨다
  (ACL 2026 실측). 문단 밖은 바이트 하나 안 바뀐다.
- **참조 전환형**: 중복 문장을 "(X.Y절 참조)" 접속으로 압축하되 문단의 고유 몫은
  유지한다 — v5c-2 정독 경고: "소거만 하면 절의 절반이 빈다".
- 새 사실 생성 경로가 없다: 압축만 허용되고, 아래 결정적 검증이 어기면 원문 유지.

결정적 안전장치(재작성 결과가 하나라도 어기면 그 문단은 원문 그대로):
1. 새 수치 금지 — significant_numbers(결과) ⊆ significant_numbers(원문)
2. 마커 보존 — (출처 n) 집합(결과) ⊆ 원문 (남긴 문장의 인용 사슬 유지)
3. 압축 방향 — 결과가 원문보다 길면 실패로 간주
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import create_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.state import ProjectState
from src.services.qa.cross_section import DUPLICATE_THRESHOLD, duplicate_pairs
from src.services.qa.gate import significant_numbers

logger = structlog.get_logger(__name__)

# 폭주 캡 — 중복이 아무리 많아도 재작성 콜은 이 안에서 끝난다(무한성 불변식).
MAX_PARAGRAPHS_PER_SECTION = 3
MAX_CALLS_PER_DOC = 20
MAX_REWRITE_TOKENS = 2000

_MARKER_RE = re.compile(r"\((?:출처|근거)\s*[0-9,\s]+\)|\[[0-9]+\]")
# 참조 접속("1.4절")의 번호가 새 수치로 오인되지 않게 검증 전에 걷어낸다.
_SECTION_REF_RE = re.compile(r"\d+\.\d+\s*절")

_SYSTEM = (
    "너는 한국어 정책보고서 문단을 다듬는 편집자다. 주어진 문단에는 앞 절에서 이미"
    " 서술된 문장들이 섞여 있다. 규칙:\n"
    '- [중복 문장] 목록에 있는 서술을 "({ref}절 참조)" 접속을 활용해 한두 문장으로'
    " 압축하라. 세부 수치·배경 설명을 반복하지 마라.\n"
    "- 문단의 고유한 내용(중복 목록에 없는 서술)은 문장 그대로 유지하라.\n"
    "- 새로운 사실·수치·주장을 추가하지 마라.\n"
    "- 남기는 문장의 (출처 n) 인용 마커는 그대로 보존하라.\n"
    "- 개조식 문체(ㅇ·- 불릿)와 격식을 유지하라.\n"
    "- 결과 문단만 출력하라. 설명·머리말 금지."
)


class RewriteTarget(NamedTuple):
    section_ref: str  # 후행 절("4.4") — 이 절의 문단을 고친다
    para_index: int  # 절 본문을 빈 줄로 가른 문단 인덱스
    counterpart_ref: str  # 정본 절("1.4") — 참조 접속의 대상
    dup_sentences: list[str]


def _order_key(ref: str) -> tuple[int, int]:
    a, _, b = ref.partition(".")
    try:
        return (int(a), int(b or 0))
    except ValueError:
        return (0, 0)


def _paragraphs(content: str) -> list[str]:
    return content.split("\n\n")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def find_targets(sections: list[tuple[str, str]]) -> list[RewriteTarget]:
    """(절 라벨, 본문) 목록 → 재작성 대상 문단들. 문서 순서상 뒤 절이 후행이다."""
    pairs = duplicate_pairs(sections, threshold=DUPLICATE_THRESHOLD)
    content_by_ref = dict(sections)
    # (후행 절, 문단 idx) → (정본 절 빈도, 중복 문장들)
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for p in pairs:
        first_is_earlier = _order_key(p.first_ref) <= _order_key(p.second_ref)
        trail_ref = p.second_ref if first_is_earlier else p.first_ref
        trail_text = p.second_text if first_is_earlier else p.first_text
        canon_ref = p.first_ref if first_is_earlier else p.second_ref
        content = content_by_ref.get(trail_ref) or ""
        norm_sent = _norm(trail_text)
        if not norm_sent:
            continue
        for i, para in enumerate(_paragraphs(content)):
            if norm_sent in _norm(para):
                slot = grouped.setdefault((trail_ref, i), {"counterparts": {}, "sentences": []})
                slot["counterparts"][canon_ref] = slot["counterparts"].get(canon_ref, 0) + 1
                if trail_text not in slot["sentences"]:
                    slot["sentences"].append(trail_text)
                break
    targets = [
        RewriteTarget(
            section_ref=ref,
            para_index=idx,
            # 가장 앞선 상대를 참조 - 빈도 최다로 고르면 후행끼리 서로 가리킬 수 있다
            # (골든 셋 실측: 4.4가 3.4를 참조했는데 3.4도 1.4의 후행이었다).
            counterpart_ref=min(slot["counterparts"], key=_order_key),
            dup_sentences=slot["sentences"],
        )
        for (ref, idx), slot in grouped.items()
    ]
    # 중복 문장이 많은 문단부터 — 캡에 걸려도 큰 덩어리가 먼저 처리되게.
    targets.sort(key=lambda t: (-len(t.dup_sentences), _order_key(t.section_ref), t.para_index))
    # 절당 캡 — 절대 수(MAX_PARAGRAPHS_PER_SECTION)에 더해 잔량 비율(문단의 25%)과
    # **인접 문단 동시 압축 금지**를 건다. 철강 2.1 실사고(2026-08-29 정독): 파트
    # 도입부 연속 3문단이 전부 "…절 참조"로 압축돼 절이 색인처럼 비었다 — 껍데기는
    # 총량이 아니라 연속 압축이 만든다.
    per_section: dict[str, int] = {}
    taken_paras: set[tuple[str, int]] = set()
    ratio_cap: dict[str, int] = {
        ref: max(1, len(_paragraphs(content)) // 4) for ref, content in sections
    }
    capped: list[RewriteTarget] = []
    for t in targets:
        limit = min(MAX_PARAGRAPHS_PER_SECTION, ratio_cap.get(t.section_ref, 1))
        if per_section.get(t.section_ref, 0) >= limit:
            continue
        if (t.section_ref, t.para_index - 1) in taken_paras or (
            t.section_ref,
            t.para_index + 1,
        ) in taken_paras:
            continue
        per_section[t.section_ref] = per_section.get(t.section_ref, 0) + 1
        taken_paras.add((t.section_ref, t.para_index))
        capped.append(t)
    return capped[:MAX_CALLS_PER_DOC]


def _validate(original: str, rewritten: str) -> str | None:
    """결정적 검증 — 통과하지 못한 사유를 돌려준다(None=통과)."""
    if not rewritten.strip():
        return "빈 결과"
    if len(rewritten) >= len(original):
        return "압축 실패(원문보다 김)"

    # 마커·참조 접속의 번호는 수치가 아니다 — 대장 수술(d90f33d)과 같은 규약.
    def _clean(text: str) -> str:
        return _SECTION_REF_RE.sub(" ", _MARKER_RE.sub(" ", text))

    new_numbers = set(significant_numbers(_clean(rewritten))) - set(
        significant_numbers(_clean(original))
    )
    if new_numbers:
        return f"새 수치 유입: {sorted(new_numbers)[:3]}"
    old_markers = set(_MARKER_RE.findall(original))
    extra_markers = set(_MARKER_RE.findall(rewritten)) - old_markers
    if extra_markers:
        return f"새 마커 유입: {sorted(extra_markers)[:3]}"
    return None


async def rewrite_paragraph(
    *,
    paragraph: str,
    dup_sentences: list[str],
    counterpart_ref: str,
    client: LLMClient | None = None,
    model: str,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> str | None:
    """문단 1개 압축 — 검증 실패·호출 실패는 None(원문 유지)."""
    try:
        llm = client or create_llm_client()
        dup_block = "\n".join(f"- {s}" for s in dup_sentences[:12])
        request = CompletionRequest(
            model=model,
            system=_SYSTEM.replace("{ref}", counterpart_ref),
            messages=[
                Message(
                    role="user",
                    content=f"[중복 문장 — {counterpart_ref}절에 이미 서술됨]\n{dup_block}\n\n"
                    f"[문단]\n{paragraph}",
                )
            ],
            max_tokens=MAX_REWRITE_TOKENS,
            temperature=0.1,
        )
        with token_context(user_id=user_id, project_id=project_id, operation="qa.dedup_rewrite"):
            response = await llm.complete(request)
        rewritten = response.content.strip()
        reason = _validate(paragraph, rewritten)
        if reason is not None:
            logger.info("dedup_rewrite.rejected", reason=reason, counterpart=counterpart_ref)
            return None
        return rewritten
    except Exception:
        logger.warning("dedup_rewrite.call_failed", exc_info=True)
        return None


async def dedup_rewrite_state(
    state: ProjectState, *, model: str, client: LLMClient | None = None
) -> tuple[ProjectState, int]:
    """조립 진입점 — 선택 확정 초안들의 중복 문단을 참조 전환으로 압축한 state.

    renumber **뒤에** 돈다 — 마커 가드는 유입만 막고 삭제는 허용(⊆)하는데, renumber의
    로컬 번호 매핑은 첫 등장 순서 규약이라 삭제에 깨진다(2026-08-27 철강 6.2 실사고:
    지워진 문단 하나로 뒤쪽 번호 전부가 옆 자료로 오귀속). 전역 번호는 삭제에 안전하다.
    실패·거부 문단은 원문 유지 — 재작성이 조립을 막는 일은 없다.
    """
    plan_by_id = {s.section_id: s for s in state.section_plan}
    labeled: list[tuple[str, str]] = []
    content_by_ref: dict[str, str] = {}
    for cset in state.section_candidates:
        chosen = state.section_selections.get(cset.section_id)
        plan = plan_by_id.get(cset.section_id)
        if plan is None:
            continue
        for cand in cset.candidates:
            if cand.candidate_id == chosen:
                ref = f"{plan.chapter_number}.{plan.section_number}"
                labeled.append((ref, cand.draft.content))
                content_by_ref[ref] = cand.draft.content
    labeled.sort(key=lambda x: _order_key(x[0]))
    targets = find_targets(labeled)
    if not targets:
        return state, 0

    n_rewritten = 0
    for t in targets:
        content = content_by_ref.get(t.section_ref)
        if content is None:
            continue
        paras = _paragraphs(content)
        if t.para_index >= len(paras):
            continue
        new_para = await rewrite_paragraph(
            paragraph=paras[t.para_index],
            dup_sentences=t.dup_sentences,
            counterpart_ref=t.counterpart_ref,
            client=client,
            model=model,
            user_id=state.user_id,
            project_id=state.project_id,
        )
        if new_para is None:
            continue
        paras[t.para_index] = new_para
        content_by_ref[t.section_ref] = "\n\n".join(paras)
        n_rewritten += 1

    if n_rewritten == 0:
        return state, 0

    ref_by_id = {s.section_id: f"{s.chapter_number}.{s.section_number}" for s in state.section_plan}
    new_sets = []
    for cset in state.section_candidates:
        chosen = state.section_selections.get(cset.section_id)
        ref = ref_by_id.get(cset.section_id)
        new_cands = []
        for cand in cset.candidates:
            if cand.candidate_id == chosen and ref in content_by_ref:
                new_cands.append(
                    cand.model_copy(
                        update={
                            "draft": cand.draft.model_copy(update={"content": content_by_ref[ref]})
                        }
                    )
                )
            else:
                new_cands.append(cand)
        new_sets.append(cset.model_copy(update={"candidates": new_cands}))
    logger.info(
        "dedup_rewrite.done",
        project_id=str(state.project_id),
        n_targets=len(targets),
        n_rewritten=n_rewritten,
    )
    return state.model_copy(update={"section_candidates": new_sets}), n_rewritten
