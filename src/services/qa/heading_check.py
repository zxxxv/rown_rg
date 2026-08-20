"""하위 헤딩 번호 결정 검사 — v5c-2 정독이 찾은 결함 계급(2026-08-20).

실측 유형 4종, 전부 결정적으로 잡힌다(LLM 불필요):
1. 결번/시작 오류 — 3.3.3만 있고 3.3.1·3.3.2가 없음(정독: "번호 체계 붕괴")
2. 고아 구획 — 4.4.1만 열고 4.4.2가 없음(하위 구획이 1개면 나눈 의미가 없다)
3. 유령 참조 — 본문이 "4.3.3의 조달 역량 진단"을 참조하는데 그 헤딩이 없음
4. 절 제목 재출력 — 본문 첫 헤딩이 절 제목을 그대로 반복(조립이 제목을 이미 단다)

severity는 전부 warning — 내용은 유효하고 표면 격식의 문제라 사람이 고치면 된다.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.types import SectionPlan

# 헤딩 줄의 "X.Y.n" — ##~#### 수준만(# 는 절 제목 수준이라 하위 구획이 아니다).
_HEADING_RE = re.compile(r"^\s{0,3}#{2,4}\s*(\d+)\.(\d+)\.(\d+)(?!\d)", re.MULTILINE)
# 본문 속 "X.Y.n" 참조(헤딩 줄 제외는 호출부에서 처리).
# (?!\d): \b는 한글 접미("4.3.3의")에서 성립하지 않는다 - 한글도 \w라 경계가 없다.
_REF_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?!\d)")
# 헤딩 줄 전체 — 제목 재출력 판정용.
_ANY_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s*(.+)$")


def _own_subsections(content: str, chapter: int, section: int) -> list[int]:
    return sorted(
        {
            int(m.group(3))
            for m in _HEADING_RE.finditer(content)
            if int(m.group(1)) == chapter and int(m.group(2)) == section
        }
    )


def _title_reprinted(content: str, title: str) -> bool:
    """본문 첫 헤딩이 절 제목을 그대로 반복하는지 — 번호·기호를 걷어내고 비교."""
    for line in content.split("\n"):
        if not line.strip():
            continue
        m = _ANY_HEADING_RE.match(line)
        if m is None:
            return False  # 첫 실내용이 헤딩이 아니면 재출력 아님
        text = re.sub(r"^[\d.\s]+", "", m.group(1)).strip()
        norm = re.sub(r"\s+", "", text)
        return bool(norm) and norm == re.sub(r"\s+", "", title)
    return False


def strip_title_reprint(content: str, title: str) -> str:
    """본문 첫 헤딩이 절 제목을 반복하면 그 줄을 걷어낸다 — 생성 직후 결정적 정규화.

    v5c-2 실측 19/20절: 우발이 아니라 작성기의 습관이라 경고로 사람을 괴롭히는 대신
    여기서 지운다(조립·미리보기가 제목을 따로 단다). 못 걷어낸 경우만
    heading_findings의 재출력 경고가 백스톱으로 잡는다.
    """
    if not content or not _title_reprinted(content, title):
        return content
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        # _title_reprinted가 참이면 첫 실내용 줄이 그 헤딩이다.
        return "\n".join(lines[:i] + lines[i + 1 :]).lstrip("\n")
    return content


def heading_findings(sections: list[tuple[SectionPlan, str]]) -> list[dict[str, Any]]:
    """(절 계획, 본문) 목록 → 헤딩 번호 경고 행들(pm_verify 행 모양)."""
    rows: list[dict[str, Any]] = []

    def add(plan: SectionPlan, detail: str) -> None:
        rows.append(
            {
                "chapter_number": plan.chapter_number,
                "severity": "warning",
                "category": "형식(헤딩 번호)",
                "section_ref": f"{plan.chapter_number}.{plan.section_number}",
                "detail": detail,
            }
        )

    for plan, content in sections:
        if not content:
            continue
        x, y = plan.chapter_number, plan.section_number
        own = _own_subsections(content, x, y)
        if own:
            expected = list(range(1, len(own) + 1))
            if own != expected:
                missing = sorted(set(range(1, max(own) + 1)) - set(own))
                add(
                    plan,
                    f"하위 헤딩 번호 결번: {x}.{y}.{{{', '.join(map(str, missing))}}}가 없이 "
                    f"{', '.join(f'{x}.{y}.{n}' for n in own)}만 존재",
                )
            if len(own) == 1:
                add(
                    plan,
                    f"고아 하위 구획: {x}.{y}.{own[0]} 하나뿐 — 구획이 1개면 번호를 "
                    "떼거나 둘 이상으로 나눠야 함",
                )
        # 유령 참조 — 헤딩 줄을 제외한 본문에서 자기 절 하위 번호를 참조하는데 헤딩이 없음
        body_wo_headings = _HEADING_RE.sub("", content)
        ghost = sorted(
            {
                int(m.group(3))
                for m in _REF_RE.finditer(body_wo_headings)
                if int(m.group(1)) == x and int(m.group(2)) == y and int(m.group(3)) not in own
            }
        )
        if ghost:
            add(
                plan,
                f"유령 하위 구획 참조: 본문이 {', '.join(f'{x}.{y}.{n}' for n in ghost)}를 "
                "참조하지만 해당 헤딩이 없음",
            )
        if _title_reprinted(content, plan.title):
            add(
                plan,
                "절 제목 재출력: 본문 첫 헤딩이 절 제목을 그대로 반복(조립이 제목을 이미 단다)",
            )
    return rows
