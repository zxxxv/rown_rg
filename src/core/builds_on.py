"""builds_on — "논지를 잇는 것은 생성물이 아니라 계획물" 계약의 순수 부품.

절이 앞 절의 확정값 위에서 쓰도록 의존을 **계획**에 표기한다(설계 확정본 2026-08-15).
예타 실측이 배경이다: 뒤 절이 앞 절 값을 딴 자료에서 다시 찾거나 창작하는 "수치 전달"
유형 8건 — 요약 체인으로 잇는 실험은 무근거 +39% 순손해였으므로, 서술이 아니라
구조화 값(사실 대장 엔트리)을 넘긴다. 이 모듈은 그 계약의 표기와 실행 순서만 담당한다
(DB·LLM 무관 — 값 추출·주입·검출은 services/ledger).

표기 3형 (SectionPlan.builds_on 원소):
- "4.1"           그 절의 확정값 전체(주입은 table/explicit만, 캡)
- "4.1(총사업비)"  지표 지정 — 사람(폼·프리셋)만 쓴다. 플래너 LLM은 절 번호만
                   허용(유령 지표 원천 차단, 2026-08-15 확정).
- "4.*"           장 전체 — X.5(시사점)류가 장의 사실 대장 전체를 받는 용도.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.types import SectionPlan

# 위상 깊이 하드캡. 실측(예타 27절+탄소 8절)에서 의존 꼬리는 전부 "별 모양"(자기 의존
# 없음, 레벨 0→1)이었다 — 2면 실데이터를 다 담고, 그 이상은 순차 배치가 길어져
# 병렬 손실만 커진다. 초과분은 절단하고 경고를 남긴다(막지 않음).
MAX_DEPTH = 2
# 절당 의존 상한 — 플래너 과잉 가드. 사람 입력(폼·프리셋)도 같은 상한을 쓴다:
# 의존이 셋 이상이면 그 절은 사실상 문서 요약이라 "4.*"가 맞다.
MAX_REFS_PER_SECTION = 2

_REF_RE = re.compile(
    r"""^\s*
    (?P<chapter>\d+)
    \.
    (?P<section>\d+|\*)
    (?:\(\s*(?P<metric>[^()]+?)\s*\))?
    \s*$""",
    re.VERBOSE,
)


@dataclass(frozen=True)
class BuildsOnRef:
    """builds_on 원소 1개의 해석. section=None이면 장 전체("4.*")."""

    chapter: int
    section: int | None
    metric: str | None

    @property
    def label(self) -> str:
        sec = "*" if self.section is None else str(self.section)
        base = f"{self.chapter}.{sec}"
        return f"{base}({self.metric})" if self.metric else base


def parse_ref(raw: str) -> BuildsOnRef | None:
    """표기 1개 해석. 못 읽으면 None — 깨진 표기가 실행을 죽이면 안 된다(절 격리 원칙)."""
    m = _REF_RE.match(raw or "")
    if m is None:
        return None
    section = None if m.group("section") == "*" else int(m.group("section"))
    metric = (m.group("metric") or "").strip() or None
    # 장 전체 참조에 지표 지정은 뜻이 없다 — 지표는 버리고 장 참조로 읽는다.
    if section is None:
        metric = None
    return BuildsOnRef(chapter=int(m.group("chapter")), section=section, metric=metric)


def clean_refs(
    raw: list[str],
    *,
    self_label: str,
    known_labels: set[str],
    known_chapters: set[int],
    allow_metric: bool = True,
) -> tuple[list[str], list[str]]:
    """builds_on 목록 정화 → (정규화된 표기, 경고 문구).

    걷어내는 것: 못 읽는 표기 · 유령 절/장 참조 · 자기 참조 · 중복 · 상한 초과.
    allow_metric=False면 지표 지정을 절 번호로 강등한다 — 플래너 LLM 경로 전용
    (지표명은 사람만 쓴다).
    """
    out: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    truncated = 0
    for raw_ref in raw:
        ref = parse_ref(str(raw_ref))
        if ref is None:
            warnings.append(f"builds_on 표기를 읽을 수 없어 무시: {raw_ref!r}")
            continue
        if not allow_metric and ref.metric:
            ref = BuildsOnRef(ref.chapter, ref.section, None)
        if ref.section is None:
            if ref.chapter not in known_chapters:
                warnings.append(f"없는 장을 참조해 무시: {ref.label}")
                continue
        else:
            base = f"{ref.chapter}.{ref.section}"
            if base == self_label:
                warnings.append(f"자기 자신 참조를 무시: {ref.label}")
                continue
            if base not in known_labels:
                warnings.append(f"없는 절을 참조해 무시: {ref.label}")
                continue
        if ref.label in seen:
            continue
        if len(out) >= MAX_REFS_PER_SECTION:
            truncated += 1
            continue
        seen.add(ref.label)
        out.append(ref.label)
    if truncated:
        warnings.append(f"절당 의존 상한({MAX_REFS_PER_SECTION})을 넘어 {truncated}건 절단")
    return out, warnings


def _dep_labels(plan_labels: dict[str, list[str]], label: str) -> set[str]:
    """한 절의 의존 표기 → 실제 절 라벨 집합("4.*"는 그 장의 모든 절, 자신 제외)."""
    deps: set[str] = set()
    for raw in plan_labels.get(label, []):
        ref = parse_ref(raw)
        if ref is None:
            continue
        if ref.section is None:
            deps.update(
                other
                for other in plan_labels
                if other != label and other.split(".")[0] == str(ref.chapter)
            )
        else:
            deps.add(f"{ref.chapter}.{ref.section}")
    deps.discard(label)
    return deps


def assign_levels(plan: list[SectionPlan]) -> tuple[dict[str, int], list[str]]:
    """절 → 실행 레벨(0부터). 레벨 n은 n-1까지 전부 끝난 뒤 시작한다.

    깊이 MAX_DEPTH 하드캡: 넘치는 절은 마지막 레벨로 강등하고 경고 — 의존 사슬이
    길다고 실행을 막으면 목차 실수 하나가 런 전체를 죽인다. 순환은 참여 절 전부를
    절단(의존 무시, 레벨 0 취급)하고 경고 — 순환 안에서는 어느 순서든 계약 위반이다.
    """
    labels = [f"{s.chapter_number}.{s.section_number}" for s in plan]
    plan_labels = {f"{s.chapter_number}.{s.section_number}": list(s.builds_on) for s in plan}
    warnings: list[str] = []
    deps = {label: _dep_labels(plan_labels, label) & set(labels) for label in labels}

    levels: dict[str, int] = {}
    resolving: set[str] = set()
    cyclic: set[str] = set()

    def level_of(label: str) -> int:
        if label in levels:
            return levels[label]
        if label in resolving:
            # 순환 — 이 절의 의존을 끊는다. 참여 절 표시는 호출부 스택이 담당.
            cyclic.add(label)
            return 0
        resolving.add(label)
        depth = 0
        for d in deps[label]:
            depth = max(depth, level_of(d) + 1)
        resolving.discard(label)
        levels[label] = depth
        return depth

    for label in labels:
        level_of(label)

    if cyclic:
        for label in sorted(cyclic):
            levels[label] = 0
            warnings.append(f"builds_on 순환을 절단(의존 무시): {label}")
    over = [label for label, lv in levels.items() if lv > MAX_DEPTH - 1]
    for label in sorted(over):
        warnings.append(
            f"의존 깊이가 {MAX_DEPTH}를 넘어 마지막 배치로 강등: {label} (레벨 {levels[label]})"
        )
        levels[label] = MAX_DEPTH - 1
    return levels, warnings


def batches(plan: list[SectionPlan]) -> tuple[list[list[SectionPlan]], list[str]]:
    """write 루프용 실행 배치 — 레벨 순, 배치 안은 병렬 안전.

    전부 레벨 0(의존 없음 — 조사분석 실측 0/0/0)이면 배치 1개 = 기존 동작 그대로라
    코드 분기가 없다.
    """
    levels, warnings = assign_levels(plan)
    if not plan:
        return [], warnings
    n_levels = max(levels.values()) + 1
    out: list[list[SectionPlan]] = [[] for _ in range(n_levels)]
    for s in plan:
        out[levels[f"{s.chapter_number}.{s.section_number}"]].append(s)
    return [b for b in out if b], warnings
