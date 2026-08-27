"""설계 변경이 본문에 아직 반영되지 않은 절을 가려낸다 — 화면 용어는 "미반영".

**왜 필요한가**: 보고서는 완성 순간이 끝이 아니다. 품질을 보고 목차를 고치거나 자료를
빼면, 그 변경이 본문에 닿기 전까지 설계와 본문이 어긋난 채로 남는다. 지금까지는 그
어긋남을 알 길이 없어 "완료면 설정 동결"로 막아 두었고, 그래서 손보려면 재개(reopen)
말고는 문이 없었다(2026-08-25 설계 전환).

**핵심 판정**: 본문이 틀렸는지가 아니라 *본문이 만들어진 뒤에 계약이 바뀌었는지*다.
그래서 "낡음"이 아니라 "미반영"이다 — 본문 자체는 그때 계약대로 정확했다.

**자동 재작성은 하지 않는다**: 절 하나 재작성이 실측 $0.67, 전체가 $15.5다. 목차 한
줄 고쳤다고 자동으로 돌면 사고다. 여기서는 무엇이 왜 미반영인지만 계산하고, 실행은
사람이 고른다(게이트·"실행은 사람이 시작한다"와 같은 철학).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from src.core.types import SectionPlan

# 미반영 사유 — 화면 문구는 이 값에 매핑한다.
#   plan_changed    목차 수정 미반영 (방향·핵심 포인트·에이전트·분량 등이 바뀜)
#   source_excluded 자료 제외 미반영 (본문이 인용한 자료가 채택에서 빠짐)
#   missing         본문 없음 (목차에 새로 넣었거나 작성이 실패한 절)
DriftReason = Literal["plan_changed", "source_excluded", "missing"]


@dataclass(frozen=True)
class SectionDrift:
    """절 1개의 미반영 상태. reasons가 비면 미반영이 아니다(목록에 넣지 않는다)."""

    section_id: UUID
    label: str  # "2.3 인구·고령화 영향" — 사람이 읽는 좌표
    reasons: tuple[DriftReason, ...]
    # 본문이 인용했는데 채택에서 빠진 자료 — 화면이 "무엇을 뺐는지" 되짚게 한다.
    excluded_source_ids: tuple[UUID, ...] = field(default=())


def content_fingerprint(plan: SectionPlan) -> str:
    """본문 작성에 영향을 주는 계획 필드의 지문.

    retrieval.rehearsal.plan_fingerprint(검색 캐시용)와 **일부러 분리**한다. 검색은
    질의에 영향을 주는 필드만 보면 되지만, 본문은 의존 계약(builds_on)과 분량 목표
    (min/max_chars)까지 바뀌면 다시 써야 한다 — 검색 결과는 그대로여도 결과물이 다르다.

    번호(chapter_number·section_number)는 넣지 않는다: 순서만 바뀐 절은 같은 본문을
    그대로 써도 된다(절 정체성은 안정 id가 지킨다, 2026-08-21).
    """
    payload = json.dumps(
        [
            plan.title,
            plan.chapter_title,
            plan.direction,
            list(plan.key_points),
            list(plan.search_queries),
            list(plan.analysts),
            list(plan.builds_on),
            plan.min_chars,
            plan.max_chars,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SectionSnapshot:
    """판정에 필요한 절 행의 최소 표면 — DB 모델을 이 모듈에 끌고 오지 않는다."""

    section_id: UUID
    has_content: bool
    plan_hash: str
    # 본문이 인용한 자료 중 지금 채택에서 빠진 것(호출부가 청크→자료 조인으로 채운다).
    excluded_source_ids: tuple[UUID, ...] = field(default=())


def detect_drift(
    plans: list[SectionPlan],
    snapshots: dict[UUID, SectionSnapshot],
) -> list[SectionDrift]:
    """현재 목차 정본과 저장된 절 행을 대조해 미반영 절을 목차 순서로 돌려준다.

    plan_hash가 빈 문자열인 행은 **지문을 기록하기 전에 쓰인 옛 절**이다. 이걸
    plan_changed로 보면 기존 보고서가 통째로 미반영으로 뜬다 — 판정하지 않는다
    (다음 재작성 때 지문이 채워지며 자연히 편입된다).
    """
    # 한 번도 안 쓴 프로젝트는 미반영이 아니라 미작성이다 - 본문이 하나도 없는데
    # "설계를 고친 뒤 본문이 아직 그 내용을 담지 않았습니다"가 12절 전부에 뜨면
    # 자료 검토 단계의 사용자가 없는 문제를 읽게 된다(2026-08-27 실물 지적: 신규
    # 프로젝트의 검토 대기 화면에 미반영 12). 본문이 하나라도 생긴 뒤부터 "새로
    # 넣었는데 안 쓴 절"(missing)이 의미를 갖는다.
    if not any(snap.has_content for snap in snapshots.values()):
        return []
    out: list[SectionDrift] = []
    for plan in plans:
        snap = snapshots.get(plan.section_id)
        reasons: list[DriftReason] = []
        excluded: tuple[UUID, ...] = ()
        if snap is None or not snap.has_content:
            reasons.append("missing")
        else:
            if snap.plan_hash and snap.plan_hash != content_fingerprint(plan):
                reasons.append("plan_changed")
            if snap.excluded_source_ids:
                reasons.append("source_excluded")
                excluded = snap.excluded_source_ids
        if reasons:
            out.append(
                SectionDrift(
                    section_id=plan.section_id,
                    label=f"{plan.chapter_number}.{plan.section_number} {plan.title}",
                    reasons=tuple(reasons),
                    excluded_source_ids=excluded,
                )
            )
    return out
