"""조립 시 결정적 세정 — 작성 잔재를 사람 손 없이 걷어낸다.

검출(게이트·PM 경고)만으로는 '사람 수정 없는 깔끔한 초안'이 안 된다 — 지워야 한다.
대상은 2026-08-15 검증런 실측 잔재: 출처 배정 메모("(출처 17 제외)"류 13건+, 3.3에만
8건), 재지정 메모("(출처 8, 19 중 8만 사용 → (출처 22))"), 마커 오염("(출превод처
25)"), 기형 callout 태그(정식 문법은 ::: callout(warn) 펜스), 내부 용어 '본 파트'.

원칙: 의미를 바꾸지 않는 결정적 치환만 한다. 템플릿을 좁게 잡아 정상 서술
("출처 12에서 제외된 품목" 같은 산문)을 먹지 않게 하고, 애매한 것은 남겨서
게이트·PM(leftover_artifacts 같은 패턴 계열)이 경고하게 둔다.
"""

from __future__ import annotations

import re

# 재지정 메모 — 목표 마커만 남긴다: "(출처 8, 19 중 8만 사용 → (출처 22))" → "(출처 22)"
_REASSIGN_RE = re.compile(r"[^\S\n]*\(출처[^()]*→\s*\(출처\s*([\d,\s]+)\)\s*\)")
# 배정 메모 — 번호 바로 뒤 조사(은/는)까지만 허용해 산문 오식을 막는다.
_MEMO_RES = (
    re.compile(r"[^\S\n]*\(출처\s*[\d,\s]+\s*(?:은|는)?\s*제외[^()]{0,30}\)"),
    re.compile(
        r"[^\S\n]*\(출처\s*[\d,\s]+\s*(?:은|는)?[^()]{0,15}(?:사용\s*불가|미사용)[^()]{0,30}\)"
    ),
    re.compile(r"[^\S\n]*\(출처\s*[\d,\s]+\s*(?:은|는)?[^()]{0,25}생략[^()]{0,10}\)"),
    # "(출처 26에 해당 없음 — 아래 항목 참조)" - v6 이관본 4.2 실측 신형(2026-08-27).
    re.compile(r"[^\S\n]*\(출처\s*[\d,\s]+\s*(?:에|은|는)?\s*해당\s*없음[^()]{0,40}\)"),
)
# 마커 오염 — "(출превод처 25" → "(출처 25". 뒤에 숫자가 와야 마커다.
_CORRUPT_MARKER_RE = re.compile(r"\(출[^\s처()]{1,12}처(?=\s*\d)")
# 기형 callout 태그 → 정식 펜스. 타입은 (warn)·type="warn" 어느 쪽이든 보존.
_CALLOUT_OPEN_RE = re.compile(r'<callout\s*(?:\(([a-z]+)\)|type="([a-z]+)")?\s*>')
_CALLOUT_CLOSE_RE = re.compile(r"</callout\s*>")
# 내부 작성 단위 용어 — 조사까지 짝을 맞춰 치환(파트/절은 받침이 달라 조사가 갈린다).
_BON_PART_SWAPS = (
    ("본 파트에서는", "이 절에서는"),
    ("본 파트에서", "이 절에서"),
    ("본 파트는", "이 절은"),
    ("본 파트를", "이 절을"),
    ("본 파트가", "이 절이"),
    ("본 파트의", "이 절의"),
    ("본 파트", "이 절"),
)


# 표 셀 값으로는 무의미한 회피 문구 — 작성 규칙이 금지하는데도 잔존한다(2026-08-21
# v6 정독, 결함 세대교체 3번). 실납품 관례인 "-"로 통일한다. 여기(조립 세정)가 정본이고
# HWPX 렌더(_clean_cell)는 세정 밖 경로(수동 편집·구버전)용 이중 방어로 남는다 —
# 생성단에서 걷어야 웹 미리보기·검사·내보내기가 같은 것을 본다.
EMPTY_CELL_VALUES = frozenset(
    {"자료 없음", "자료상 미제시", "상동", "해당 없음", "해당사항 없음", "N/A", "n/a"}
)
# 표 행 판별 - 셀 단위로만 치환한다. 산문의 "자료 없음"은 문장의 일부라 안 건드린다.
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _scrub_table_cells(content: str) -> tuple[str, int]:
    lines = content.splitlines()
    n = 0
    for i, line in enumerate(lines):
        if not _TABLE_ROW_RE.match(line):
            continue
        cells = line.split("|")
        changed = False
        for j, cell in enumerate(cells):
            if cell.strip() in EMPTY_CELL_VALUES:
                # 원래 폭을 흉내 내 파이프 정렬을 덜 흔든다(렌더에는 무영향, diff 가독용).
                cells[j] = cell.replace(cell.strip(), "-")
                changed = True
                n += 1
        if changed:
            lines[i] = "|".join(cells)
    if not n:
        return content, 0
    joined = chr(10).join(lines)
    if content.endswith(chr(10)):
        joined += chr(10)
    return joined, n


def scrub_leftovers(content: str) -> tuple[str, list[str]]:
    """(세정된 본문, 세정 내역). 내역이 비면 손대지 않은 것."""
    notes: list[str] = []
    out = content

    def _sub(pattern: re.Pattern[str], repl: str, label: str, text: str) -> str:
        new, n = pattern.subn(repl, text)
        if n:
            notes.append(f"{label} {n}건")
        return new

    out = _sub(_REASSIGN_RE, r" (출처 \1)", "출처 재지정 메모 정리", out)
    for i, memo in enumerate(_MEMO_RES):
        out = _sub(memo, "", f"출처 배정 메모 제거({i + 1})", out)
    out = _sub(_CORRUPT_MARKER_RE, "(출처", "오염 마커 복구", out)

    def _open(m: re.Match[str]) -> str:
        kind = m.group(1) or m.group(2) or "warn"
        return f"::: callout({kind})"

    new, n = _CALLOUT_OPEN_RE.subn(_open, out)
    if n:
        notes.append(f"callout 태그 정식 펜스화 {n}건")
    out = new
    out = _sub(_CALLOUT_CLOSE_RE, ":::", "callout 닫는 태그 정리", out)

    swapped = 0
    for old, new_s in _BON_PART_SWAPS:
        if old in out:
            swapped += out.count(old)
            out = out.replace(old, new_s)
    if swapped:
        notes.append(f"'본 파트' 용어 치환 {swapped}건")

    out, n_cells = _scrub_table_cells(out)
    if n_cells:
        notes.append(f"표 금지 셀 정리 {n_cells}건")
    return out, notes
