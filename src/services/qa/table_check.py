"""표 셀 수치 검사 — 셀 값을 본문·인용 근거와 결정적으로 대조한다 (LLM 없음).

표 행은 '주장 단위'가 아니라 문장 검사망(무근거 수치·근거 대조) 전부에서 빠져
있었다. 그 사이 실제 결함은 표에서 났다 — 분할 작성이 검색 풀을 파트별로 쪼개
같은 지표가 표와 본문에서 다른 값이 되는 사고(탄소규제 런 실측, 백로그 3번).

두 검사 모두 **정밀도 우선**이다:
- 표-본문 대조(B)는 행 라벨이 문장에 그대로 등장하고 같은 형(퍼센트/일반)의
  수치가 있을 때만 판정한다. 애매한 쌍을 다 세면 경고 폭탄이 돼 아무도 안 읽는다
  (critical 26건 중 22건 오탐 실사고와 같은 실패 양식).
- 근거 대조(A)는 어휘 부분문자열이라 단위 환산(72억 vs $7.2B)을 원리적으로 못
  재므로 경고용이다 — 문장 쪽 무근거 수치 검사와 같은 눈금.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.citations import MARK_RE
from src.services.qa.gate import claim_units, normalize_number, significant_numbers

_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEP_CELL_RE = re.compile(r"^\s*:?-{2,}:?\s*$")
# 라벨 정리 - 괄호 병기(영문명 등)·강조 표기를 걷어 본문 문장과 맞출 수 있게 한다.
_PAREN_RE = re.compile(r"\([^)]*\)")
_EMPHASIS_RE = re.compile(r"[*_`]")


@dataclass
class TableCell:
    """수치를 품은 표 셀 하나 - 행 라벨·열 머리가 이 수치의 '지표 이름'이다."""

    row_label: str
    col_header: str
    token: str  # 원문 표기("1,350억"이면 콤마 포함)
    norm: str  # 매칭용 정규화(콤마·후행 % 제거)
    is_percent: bool


def _cells_of(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _clean_label(cell: str) -> str:
    """행 라벨 → 본문 대조용 핵심 표기. 괄호 병기·강조를 걷고 공백을 접는다."""
    text = _EMPHASIS_RE.sub("", _PAREN_RE.sub("", cell))
    return re.sub(r"\s+", " ", text).strip()


def table_numeric_cells(content: str) -> list[TableCell]:
    """본문의 모든 표에서 수치 셀을 뽑는다 - 연도·용어숫자는 significant_numbers가 거른다."""
    out: list[TableCell] = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        if not _TABLE_LINE_RE.match(lines[i]):
            i += 1
            continue
        block: list[list[str]] = []
        while i < len(lines) and _TABLE_LINE_RE.match(lines[i]):
            block.append(_cells_of(lines[i]))
            i += 1
        if len(block) < 2:
            continue
        headers = block[0]
        for row in block[1:]:
            if all(_SEP_CELL_RE.match(c) or not c for c in row):
                continue
            label = _clean_label(row[0]) if row else ""
            for col, cell in enumerate(row[1:], start=1):
                header = _clean_label(headers[col]) if col < len(headers) else ""
                for token in significant_numbers(cell):
                    out.append(
                        TableCell(
                            row_label=label,
                            col_header=header,
                            token=token,
                            norm=normalize_number(token),
                            is_percent=token.endswith("%"),
                        )
                    )
    return out


def table_ungrounded_numbers(content: str, cited_content: str) -> list[str]:
    """검사 A: 인용 근거 어디에도 없는 표 셀 수치 (등장 순서·중복 제거).

    문장 쪽 ungrounded_numbers와 같은 자(콤마 정규화 후 부분문자열)로 잰다.
    반환은 "수치(행 라벨/열 머리)" 표기 - 어느 셀인지 사람이 바로 찾게.
    """
    if not cited_content.strip():
        return []
    haystack = cited_content.replace(",", "")
    out: list[str] = []
    seen: set[str] = set()
    for cell in table_numeric_cells(content):
        if not cell.norm or cell.norm in seen:
            continue
        seen.add(cell.norm)
        if cell.norm not in haystack:
            where = "/".join(p for p in (cell.row_label, cell.col_header) if p)
            out.append(f"{cell.token}({where})" if where else cell.token)
    return out


def _values_close(a: str, b: str) -> bool:
    """반올림·절사 차이는 불일치가 아니다.

    본문의 '약 46%'와 표의 45.8%는 같은 값이고, '482억 7,000만'과 482.7도 같은
    값이다(한국식 억/만 분해 - 실측에서 이게 오탐 1순위였다). 반올림 또는 절사가
    같으면 일치로 본다.
    """
    if a == b:
        return True
    try:
        fa, fb = float(a), float(b)
    except ValueError:
        return False
    return round(fa) == round(fb) or int(fa) == int(fb)


def _valid_key(text: str) -> bool:
    return len(text) >= 2 and bool(re.search(r"[가-힣A-Za-z]", text))


def table_prose_mismatches(content: str) -> list[str]:
    """검사 B: 같은 지표를 말하는 본문 문장과 표 셀의 수치가 다른 경우.

    판정 조건(전부 만족해야 경고 - 정밀도 우선):
    1. 행 라벨과 **열 머리 둘 다** 문장에 그대로 등장 - 같은 지표라는 확신의 기준.
       라벨만 보면 '미국'·'한국' 같은 일반 명사가 온갖 문장과 짝지어져 다른 지표의
       수치끼리 비교된다(2026-08-14 로컬 6종 실측: 경고의 대부분이 이 오탐이었다).
    2. 그 문장에 셀과 같은 형(퍼센트/일반)의 유의미 수치가 있음
    3. 그런 어떤 문장에서도 셀 값(반올림·절사 허용)이 확인되지 않음
    """
    cells = table_numeric_cells(content)
    if not cells:
        return []
    sentences = [(s, MARK_RE.sub(" ", s)) for s in claim_units(content)]
    out: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for cell in cells:
        label, header = cell.row_label, cell.col_header
        if not (_valid_key(label) and _valid_key(header)):
            continue
        key = (label, header, cell.norm)
        if key in seen:
            continue
        matched = False  # 라벨+열 머리+같은 형 수치가 있는 문장을 봤는가
        consistent = False
        example: tuple[str, str] | None = None
        for raw, bare in sentences:
            if label not in bare or header not in bare:
                continue
            same_shape = [
                (t, normalize_number(t))
                for t in significant_numbers(bare)
                if t.endswith("%") == cell.is_percent
            ]
            if not same_shape:
                continue
            matched = True
            if any(_values_close(n, cell.norm) for _, n in same_shape):
                consistent = True
                break
            if example is None:
                example = (raw, same_shape[0][0])
        if matched and not consistent and example is not None:
            seen.add(key)
            raw, other = example
            out.append(f"표 '{label}/{header}' {cell.token} vs 본문 {other} (\"{raw[:40]}…\")")
    return out
