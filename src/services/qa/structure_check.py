"""절 서두 선언과 본문 이행의 대조 — 구조 완결성(2026-08-29 철강 정독의 최대 구멍).

정독 실측 결함 3건이 전부 PM·정적 게이트 밖이었다:
- 2.1 STEEP: 서두가 "사회·기술·경제·환경·정치 5개 요인"을 선언하고 본문엔 기술·환경뿐
- 6.1 SWOT: "SO·ST·WO·WT 교차 전략을 확정함" 선언 후 WT 블록 부재
- 6.2: 소제목 "### 가."만 있고 나·다가 없음(4.2는 가·나·다를 갖췄다)

선언은 절이 스스로 세운 계약이다 — 자료가 없어 못 채웠으면 선언을 줄였어야 하고,
채웠는데 검사가 못 보면 독자가 처음 발견한다. LLM 없이 결정적으로 잰다.

판정 원칙(정밀도 우선):
- 선언은 절 서두(도입 구간)에서만 읽는다 — 본문 중간의 열거는 구조 계약이 아니다.
- 항목 이행은 '블록 앵커'(줄머리 근처 등장)로만 인정한다. 산문 속 스침("정치권의
  논의")은 이행이 아니다 — 다만 그 방향의 오탐(스침을 이행으로 오인)은 놓침이 되고,
  놓침은 경고 하나가 사라질 뿐이라 싸다.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from src.core.types import SectionPlan

# 선언을 읽는 서두 구간 — 절 도입부. 이보다 뒤의 열거는 본문 서술로 본다.
_INTRO_CHARS = 600

# 열거 선언 — "사회·기술·경제·환경·정치 5개 요인" / "SO·ST·WO·WT 교차 전략".
# 가운뎃점(·) 열거 3개 이상 + 구조 종류어가 25자 안에 따라올 때만 계약으로 읽는다.
# 쉼표 열거는 받지 않는다 — 예시 나열("자동차, 건설, 가전 등")과 못 가른다.
_ENUM_DECL_RE = re.compile(
    r"(?P<items>[가-힣A-Za-z]{1,14}(?:\s*·\s*[가-힣A-Za-z]{1,14}){2,})"
    r"[^\n·]{0,25}?(?:(?P<count>\d+)\s*(?:개|가지|대))?\s*"
    r"(?P<kind>요인|축|부문|영역|전략|단계|유형|관점|측면)"
)

# 항목 이행 앵커 — 줄머리(마커·헤딩 기호 포함 8자 이내)에 항목이 등장하는 줄.
_ANCHOR_PREFIX = 8

# 가나다 소제목 — "### 가. 제목" 꼴. 한 벌 시작하면 이어져야 한다.
_GANADA_ORDER = "가나다라마바사아자차"
_GANADA_HEADING_RE = re.compile(rf"^\s{{0,3}}#{{1,4}}\s*([{_GANADA_ORDER}])\.\s", re.MULTILINE)


class EnumDeclaration(NamedTuple):
    """서두가 선언한 구조 계약 한 건."""

    items: tuple[str, ...]
    kind: str
    count: int | None  # "5개 요인"의 5. 없으면 None
    raw: str


def declared_enumerations(content: str) -> list[EnumDeclaration]:
    """절 서두의 열거 선언들 — 본문이 이행해야 할 구조 계약."""
    intro = (content or "")[:_INTRO_CHARS]
    out: list[EnumDeclaration] = []
    for m in _ENUM_DECL_RE.finditer(intro):
        items = tuple(t.strip() for t in m.group("items").split("·") if t.strip())
        if len(items) < 3:
            continue
        count = int(m.group("count")) if m.group("count") else None
        out.append(EnumDeclaration(items, m.group("kind"), count, m.group().strip()))
    return out


def _anchored(item: str, body_lines: list[str]) -> bool:
    """항목이 어떤 줄의 머리(마커 포함 앞 8자) 근처에 등장하는가 — 블록 이행 판정."""
    for line in body_lines:
        idx = line.find(item)
        if 0 <= idx <= _ANCHOR_PREFIX:
            return True
    return False


def unfulfilled_items(content: str) -> list[tuple[EnumDeclaration, list[str]]]:
    """선언했는데 본문에 블록이 없는 항목들 — [(선언, 빠진 항목들)].

    서두 선언 줄 자체는 앵커가 될 수 없다(선언과 이행은 다른 줄이다).
    """
    text = content or ""
    decls = declared_enumerations(text)
    if not decls:
        return []
    decl_raws = {d.raw for d in decls}
    body_lines = [line for line in text.split("\n") if not any(raw in line for raw in decl_raws)]
    out: list[tuple[EnumDeclaration, list[str]]] = []
    for decl in decls:
        missing = [item for item in decl.items if not _anchored(item, body_lines)]
        if missing:
            out.append((decl, missing))
    return out


def count_mismatches(content: str) -> list[EnumDeclaration]:
    """'N개 요인'이라며 M개를 열거한 선언 — 선언 안에서 이미 어긋난 계약."""
    return [
        d for d in declared_enumerations(content) if d.count is not None and d.count != len(d.items)
    ]


def ganada_breaks(content: str) -> list[str]:
    """가나다 소제목 벌의 중단·결번 설명 목록.

    실측(2026-08-29 철강 6.2): "### 가."만 있고 나·다가 없어 소제목이 절 내용의
    80%를 잘못 이름 붙였다. 하나만 열었으면 체계를 접었어야 한다.
    """
    found = [m.group(1) for m in _GANADA_HEADING_RE.finditer(content or "")]
    if not found:
        return []
    seen = sorted({_GANADA_ORDER.index(ch) for ch in found})
    out: list[str] = []
    if len(seen) == 1:
        out.append(f"소제목 체계 중단: '{_GANADA_ORDER[seen[0]]}.'만 있고 다음 소제목이 없음")
        return out
    expected = list(range(seen[0], seen[0] + len(seen)))
    if seen != expected or seen[0] != 0:
        missing = sorted(set(range(0, max(seen) + 1)) - set(seen))
        if missing:
            out.append(
                "소제목 결번: "
                + ", ".join(f"'{_GANADA_ORDER[i]}.'" for i in missing)
                + " 없이 "
                + ", ".join(f"'{_GANADA_ORDER[i]}.'" for i in seen)
                + " 존재"
            )
    return out


def structure_findings(sections: list[tuple[SectionPlan, str]]) -> list[dict[str, Any]]:
    """(절 계획, 본문) 목록 → 구조 완결성 경고 행들(pm_verify 행 모양).

    빠진 항목 2개 이상이면 critical — 선언한 구조의 상당 부분이 비어 있다는 뜻이고,
    실측에서 그 절(2.1 STEEP)은 절 제목 자체가 성립하지 않았다.
    """
    rows: list[dict[str, Any]] = []

    def add(plan: SectionPlan, severity: str, detail: str) -> None:
        rows.append(
            {
                "chapter_number": plan.chapter_number,
                "severity": severity,
                "category": "구조 완결성",
                "section_ref": f"{plan.chapter_number}.{plan.section_number}",
                "detail": detail,
            }
        )

    for plan, content in sections:
        if not content:
            continue
        for decl, missing in unfulfilled_items(content):
            add(
                plan,
                "critical" if len(missing) >= 2 else "warning",
                f"서두가 선언한 {decl.kind}({'·'.join(decl.items)}) 중 본문에 블록이 없음: "
                f"{', '.join(missing)} — 자료가 없어 못 채웠으면 선언에서 빼고, "
                "채울 수 있으면 절 재작성으로 채울 것",
            )
        for decl in count_mismatches(content):
            add(
                plan,
                "warning",
                f"선언 개수 불일치: '{decl.count}개 {decl.kind}'라며 "
                f"{len(decl.items)}개({'·'.join(decl.items)})를 열거",
            )
        for why in ganada_breaks(content):
            add(plan, "warning", why)
    return rows
