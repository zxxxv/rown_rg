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
                # 셀 안 인용 마커 "(출처 35, 13)"의 번호는 수치가 아니다 - v2 검증런
                # 실측에서 표 무근거 9건 중 4건(값 9개 전부)이 이 오인이었다(2026-08-15).
                for token in significant_numbers(MARK_RE.sub(" ", cell)):
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


# 구성비 열임을 알리는 머리말 — 이 말이 붙은 열만 합계를 잰다. 성장률·증감률 열을
# 더하면 뜻이 없으므로 머리말로 좁힌다(정밀도 우선).
_SHARE_HEADER_RE = re.compile(r"비중|점유율|구성비|구성\s*비율|비율|share|portion|mix", re.I)
# 합계·소계 행은 검산 대상이 아니라 답이다 — 같이 더하면 두 배가 된다.
_TOTAL_ROW_RE = re.compile(
    r"^(?:합\s*계|계|총\s*계|소\s*계|전\s*체|누\s*계|total|sum|subtotal)$|합산", re.I
)
# 구성비로 볼 최소 항목 수·허용 오차(%p). 반올림 누적을 감안해 2%p까지는 눈감는다.
# 항목 4개 이상만 보는 이유(2026-08-24 예타 실측): 세대별·국가별로 **각자 독립된**
# 비율을 적은 열(1세대 75%·2세대 63%…)이 3개짜리로 흔한데, 그걸 더하면 뜻이 없다.
_SHARE_MIN_ITEMS = 4
_SHARE_TOLERANCE = 2.0
# 구성비로 볼 합계 범위 — 이 밖이면 애초에 전체를 나눈 표가 아니다(205%·37%·28%는
# 각 행이 제 몫을 따로 말하거나 목록의 일부만 실은 것이라 검산 대상이 아니다).
_SHARE_SUM_BAND = (90.0, 110.0)


def table_share_sum_mismatches(content: str) -> list[str]:
    """구성비 열의 합이 100%에서 벗어난 표 — 원출처 오류까지 잡아내는 검산.

    COMPA 런 실측(2026-08-24): 권역별 점유율 합이 106%였는데 어떤 검사도 못 봤다.
    원출처(marketintelo)가 틀린 값을 실었고 본문은 그것을 충실히 인용했다 — 근거
    대조는 "근거에 있으니 통과"로 끝나므로, 표 안에서 스스로 앞뒤가 맞는지는
    따로 세야 한다. 성장률 열을 더하면 뜻이 없으니 구성비 머리말이 붙은 열만 본다.
    """
    by_column: dict[str, list[tuple[str, float]]] = {}
    for cell in table_numeric_cells(content):
        if not cell.is_percent or not _SHARE_HEADER_RE.search(cell.col_header):
            continue
        if _TOTAL_ROW_RE.search(cell.row_label.strip()):
            continue
        try:
            value = float(cell.norm)
        except ValueError:
            continue
        by_column.setdefault(cell.col_header, []).append((cell.row_label, value))

    out: list[str] = []
    for header, items in by_column.items():
        if len(items) < _SHARE_MIN_ITEMS:
            continue
        total = sum(v for _label, v in items)
        if abs(total - 100.0) <= _SHARE_TOLERANCE:
            continue
        if not _SHARE_SUM_BAND[0] <= total <= _SHARE_SUM_BAND[1]:
            continue  # 전체를 나눈 표가 아니다 — 검산할 '100%'가 애초에 없다
        shown = ", ".join(f"{label} {value:g}%" for label, value in items[:4])
        out.append(
            f"'{header}' 열의 합이 {total:g}% (항목 {len(items)}개: {shown}"
            + (" …" if len(items) > 4 else "")
            + ")"
        )
    return out


def table_ungrounded_numbers(content: str, cited_content: str) -> list[str]:
    """검사 A: 인용 근거 어디에도 없는 표 셀 수치 (등장 순서·중복 제거).

    문장 쪽 ungrounded_numbers와 같은 자(콤마 정규화 후 부분문자열)로 잰다.
    반환은 "수치(행 라벨/열 머리)" 표기 - 어느 셀인지 사람이 바로 찾게.
    """
    if not cited_content.strip():
        return []
    # 인용 근거가 외국어뿐이면 어휘로 '없다'를 선언할 수 없다 - $370B vs 3,700억
    # 달러 같은 단위 환산이 전부 오탐이 된다(2026-08-15 실측: 표 무근거 5건/값 20개
    # 전부 이 유형). 문장 축의 crosslingual 원칙 그대로 판정을 포기한다.
    if not re.search(r"[가-힣]", cited_content):
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


def _same_band(a: str, b: str) -> bool:
    """×10 배율 밴드 안에 있어야 같은 지표의 값 후보다.

    라벨·열 머리가 겹쳐도 배율이 크게 다르면 다른 지표다(2026-08-15 실측: 표-본문
    오짝 4건 전부 16vs650·8%vs200%·570vs37처럼 10배 이상). 실제 결함은 같은 지표의
    근소한 충돌(1.8조vs2.7조, 74vs626개사)이라 밴드 안에 남는다.
    """
    try:
        fa, fb = abs(float(a)), abs(float(b))
    except ValueError:
        return False
    if fa == 0 or fb == 0:
        return fa == fb
    ratio = fa / fb if fa >= fb else fb / fa
    return ratio < 10


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
                if t.endswith("%") == cell.is_percent and _same_band(normalize_number(t), cell.norm)
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


# ── 표 합산·복제·빈 목록 검산 (2026-09-04 철강 R&D 정독 실측 3종) ──────────────

# 본문 총계 주장: "전체 수요 매칭은 87건", "총 45개 품목" 류. 수량사까지 요구해
# 정밀도를 지킨다(연도·비율이 총계로 오인되지 않게).
_PROSE_TOTAL_RE = re.compile(
    r"(?:총|전체|도합|모두|합계)\s*[가-힣 ]{0,12}?(\d[\d,]*)\s*(?:건|개|명|곳|개사|과제|품목|종)"
)
# 덧셈 검산식: "16+8+3+13+5 = 45" — 보고서가 스스로 보여준 산술은 공짜로 검증된다.
_ADDITION_RE = re.compile(r"(\d[\d,]*(?:\s*\+\s*\d[\d,]*)+)\s*=\s*(\d[\d,]*)")
# 열 합산을 신뢰할 최소 항목 수 — 2개짜리는 표가 아니라 나열이다.
_SUM_MIN_ITEMS = 3
# 총계 주장을 표와 짝지을 최대 줄 거리(표 블록 끝 기준).
_TOTAL_CLAIM_WINDOW = 6


def _table_blocks(content: str) -> list[tuple[int, int, list[list[str]]]]:
    """(시작 줄, 끝 줄, 셀 행렬) 목록 — 구분선 행은 셀 행렬에서 뺀다."""
    lines = content.split("\n")
    out: list[tuple[int, int, list[list[str]]]] = []
    i = 0
    while i < len(lines):
        if not _TABLE_LINE_RE.match(lines[i]):
            i += 1
            continue
        start = i
        block: list[list[str]] = []
        while i < len(lines) and _TABLE_LINE_RE.match(lines[i]):
            cells = _cells_of(lines[i])
            if not all(_SEP_CELL_RE.match(c) or not c for c in cells):
                block.append(cells)
            i += 1
        if len(block) >= 2:
            out.append((start, i - 1, block))
    return out


# 셀 전체가 '수 하나(+짧은 수량사)'인 꼴 — significant_numbers는 한 자리 수를
# 변별력 없다고 버리는데(문장 대조용 눈금), 건수 열의 8건·5건은 합산에 필요하다.
_WHOLE_CELL_NUM_RE = re.compile(
    r"^(\d[\d,]*(?:\.\d+)?)\s*(?:건|개|명|곳|개사|과제|품목|종|회|억원|억 원|억)?$"
)


def _cell_number(cell: str) -> float | None:
    """셀이 '값 하나'일 때만 그 수 — 서술·복수 수치·비율 셀은 합산에 안 넣는다."""
    text = MARK_RE.sub(" ", cell).strip()
    m = _WHOLE_CELL_NUM_RE.match(text)
    if m is None:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def table_total_mismatches(content: str) -> list[str]:
    """열 합산 vs 합계 행·본문 총계 주장·덧셈식 — 산술로 갈리는 자기모순 검산.

    2026-09-04 철강 R&D 실측: 표가 16+8+3+13+5=45건을 검산식까지 보여주고 바로
    다음 줄이 "전체 87건"이라 주장했는데 어떤 검사도 못 봤다 — 셀 단위 대조
    (table_prose_mismatches)는 라벨+열 머리가 문장에 있어야만 봐서, '총계'라는
    새 이름을 단 주장은 짝을 못 찾는다. 합산은 산술이라 방향까지 확정된다.
    """
    out: list[str] = []
    lines = content.split("\n")
    for start, end, block in _table_blocks(content):
        headers = block[0]
        data_rows = []
        total_row = None
        for row in block[1:]:
            label = _clean_label(row[0]) if row else ""
            if _TOTAL_ROW_RE.search(label.strip()):
                total_row = row
            else:
                data_rows.append(row)
        if len(data_rows) < _SUM_MIN_ITEMS:
            continue
        col_sums: dict[int, float] = {}
        for col in range(1, max(len(r) for r in data_rows)):
            values = [
                v for r in data_rows if col < len(r) and (v := _cell_number(r[col])) is not None
            ]
            if len(values) >= _SUM_MIN_ITEMS:
                col_sums[col] = sum(values)
        # 1) 합계 행 검산
        if total_row is not None:
            for col, total in col_sums.items():
                if col < len(total_row) and (shown := _cell_number(total_row[col])) is not None:
                    if abs(shown - total) > 0.5:
                        header = _clean_label(headers[col]) if col < len(headers) else f"{col}열"
                        out.append(f"'{header}' 열 합 {total:g} vs 합계 행 {shown:g}")
        # 2) 표 근처 본문의 총계 주장 검산 — 표와 같은 수 밴드(×10 이내)일 때만
        lo = max(0, start - _TOTAL_CLAIM_WINDOW)
        hi = min(len(lines), end + 1 + _TOTAL_CLAIM_WINDOW)
        for ln in lines[lo:start] + lines[end + 1 : hi]:
            if _TABLE_LINE_RE.match(ln):
                continue
            for m in _PROSE_TOTAL_RE.finditer(ln):
                claim = float(m.group(1).replace(",", ""))
                for col, total in col_sums.items():
                    if total <= 0 or claim == total:
                        continue
                    ratio = max(claim, total) / max(min(claim, total), 0.1)
                    if ratio < 10 and abs(claim - total) > 0.5:
                        header = _clean_label(headers[col]) if col < len(headers) else f"{col}열"
                        out.append(
                            f"본문 총계 주장 {m.group(1)} vs '{header}' 열 합 {total:g}"
                            f' ("{ln.strip()[:40]}…")'
                        )
    # 3) 덧셈식 검산 — 표 밖 포함 전 본문
    for m in _ADDITION_RE.finditer(content):
        terms = [float(t.replace(",", "")) for t in re.split(r"\s*\+\s*", m.group(1))]
        shown = float(m.group(2).replace(",", ""))
        if abs(sum(terms) - shown) > 0.5:
            out.append(f"덧셈식 불일치: {m.group(1)} = {m.group(2)} (실제 {sum(terms):g})")
    return out


# 복제 판정 셀 최소 길이 — "해당 없음"·"-" 같은 정상 반복 값을 걸러낸다.
_DUP_CELL_MIN_CHARS = 10


def table_duplicate_row_cells(content: str) -> list[str]:
    """같은 표에서 두 행의 서술 셀 문안이 동일 — 옆 행 복사의 서명.

    2026-09-04 실측: CBAM과 GSSA 비교표의 '목적' 칸이 토씨까지 동일했고, 그
    복제가 본문의 발효일 오귀속(CBAM 날짜가 GSSA에 붙음)과 같은 뿌리였다.
    수치 셀·짧은 값은 정상 반복이 흔해 서술형(길이 10+·비수치)만 본다.
    """
    out: list[str] = []
    for _start, _end, block in _table_blocks(content):
        headers = block[0]
        rows = block[1:]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                la, lb = _clean_label(a[0]) if a else "", _clean_label(b[0]) if b else ""
                if not la or not lb or la == lb:
                    continue
                for col in range(1, min(len(a), len(b))):
                    ca = re.sub(r"\s+", " ", a[col]).strip()
                    cb = re.sub(r"\s+", " ", b[col]).strip()
                    if (
                        ca
                        and ca == cb
                        and len(ca) >= _DUP_CELL_MIN_CHARS
                        and re.search(r"[가-힣A-Za-z]", ca)
                        and _cell_number(ca) is None
                    ):
                        header = _clean_label(headers[col]) if col < len(headers) else f"{col}열"
                        out.append(f"'{la}'와 '{lb}' 행의 '{header}' 칸 문안 동일: \"{ca[:30]}…\"")
        # 전치형 표(비교 대상이 열로 놓인 꼴)의 옆 칸 복사 — 같은 행의 두 열이
        # 동일 문안이면 한쪽이 다른 쪽을 베낀 서명이다(2026-09-04 실측: CBAM과
        # GSSA 비교표의 '목적' 행에서 두 제도 칸이 토씨까지 동일).
        for row in rows:
            label = _clean_label(row[0]) if row else ""
            if not label:
                continue
            for ci in range(1, len(row)):
                for cj in range(ci + 1, len(row)):
                    ca = re.sub(r"\s+", " ", row[ci]).strip()
                    cb = re.sub(r"\s+", " ", row[cj]).strip()
                    if (
                        ca
                        and ca == cb
                        and len(ca) >= _DUP_CELL_MIN_CHARS
                        and re.search(r"[가-힣A-Za-z]", ca)
                        and _cell_number(ca) is None
                    ):
                        ha = _clean_label(headers[ci]) if ci < len(headers) else f"{ci}열"
                        hb = _clean_label(headers[cj]) if cj < len(headers) else f"{cj}열"
                        out.append(f"'{label}' 행의 '{ha}'와 '{hb}' 칸 문안 동일: \"{ca[:30]}…\"")
    return out


# 목록 선언: "확정 개발기술 목록(20개 품목)" 류 — 개수 선언이 있는 줄.
_DECLARED_LIST_RE = re.compile(r"목록\s*[（(]\s*(\d{1,3})\s*개[^）)]*[）)]")
_BULLET_RE = re.compile(r"^\s*(?:[-*·ㅇ○◦□]|\d{1,2}[.)])\s+\S")
# 개조식 마커 깊이 — 선언 줄과 같은 깊이의 뒤 불릿은 목록 항목이 아니라 형제
# 서술이다(2026-09-04 실측: 빈 목록 선언 두 줄 뒤의 형제 ㅇ 불릿을 항목으로
# 오인해 놓침). 항목은 선언보다 깊은 마커·번호 목록·표만 인정한다.
_LIST_MARKER_DEPTH = {"□": 0, "ㅇ": 1, "○": 1, "◦": 1, "-": 2, "·": 2, "*": 3}


def _marker_depth(line: str) -> int | None:
    m = re.match(r"^\s*([□ㅇ○◦·*-])\s+", line)
    return _LIST_MARKER_DEPTH.get(m.group(1)) if m else None


def declared_lists_unfilled(content: str) -> list[str]:
    """ "목록(N개)" 선언 직후에 항목이 하나도 없는 곳 — 유실된 산출물의 서명.

    2026-09-04 실측: "내역① 확정 개발기술 목록(20개 품목)" 선언 아래가 통째로
    빈칸이었고, 후속 절이 그 목록을 재인용한다고 서술했다. 부족(N 미달)은
    나열 방식이 다양해 오탐이 많으므로 '0건'만 확정 결함으로 본다.
    """
    out: list[str] = []
    lines = content.split("\n")
    for i, line in enumerate(lines):
        m = _DECLARED_LIST_RE.search(line)
        if not m:
            continue
        decl_depth = _marker_depth(line)
        n_items = 0
        for follow in lines[i + 1 : i + 8]:
            if not follow.strip():
                continue
            if _TABLE_LINE_RE.match(follow) or re.match(r"^\s*\d{1,2}[.)]\s+\S", follow):
                n_items += 1
                continue
            depth = _marker_depth(follow)
            if depth is not None and (decl_depth is None or depth > decl_depth):
                n_items += 1
                continue
            break
        if n_items == 0:
            out.append(f'{m.group(1)}개 목록 선언 직후가 빈칸: "{line.strip()[:40]}…"')
    return out
