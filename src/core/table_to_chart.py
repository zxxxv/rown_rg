"""표 → 차트 자동 변환 — 프론트 tableToChart.ts의 파이썬 짝, 그 위에 "확신할 때만" 판정.

두 층으로 나뉜다.

1. **이식층**(find_table·numeric_columns·default_choice·ambiguous_cell·build_spec):
   tableToChart.ts와 같은 규칙이다. 두 쪽이 어긋나면 화면에서 사람이 만든 그래프와
   서버가 만든 그래프가 다른 값을 그린다 — 같은 표에서 같은 스펙이 나와야 한다.

2. **판정층**(auto_choice): 사람이 없을 때만 필요한 것. 원래 철학은 "자동으로 바꾸지
   않는다 — 유형 선택은 사람 몫"이었고 그 판단은 지금도 옳다. 다만 검증런은 무인이라
   아무도 안 누르고, 그래서 표 97개·차트 0개가 나왔다(6·7차 실측). 그래서 **사람이
   봐도 이견이 없을 표만** 바꾸고 나머지는 표로 남긴다. 애매하면 안 바꾸는 쪽이 기본값이다.

값은 **표 셀에서 그대로** 온다. 모델이 수치를 다시 적는 경로는 열지 않는다 — 차트 값은
근거에 매인 수치이고, 전사 한 번이면 조용히 틀린 그래프가 된다. 원본 블록은 spec.table에
통째로 남아 "표로 되돌리기"와 못 그릴 때의 폴백이 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.charts import MAX_SERIES, ChartSeries, ChartSpec, to_fence
from src.core.citations import MARK_RE

# ── 이식층: tableToChart.ts와 같은 규칙 ────────────────────────────────────────

# 표 제목 줄 — "표: 제목", "[표 2-1] 제목" 둘 다 받는다(MarkdownContent와 같은 규약).
_CAPTION_RE = re.compile(r"^(?:\[\s*표[^\]]*\]|표\s*[\d-]*\s*[:：])\s*(.+)$")
_SOURCE_MARK_RE = re.compile(r"\(출처\s*(\d{1,3}(?:\s*,\s*\d{1,3})*)\s*\)")
_SEPARATOR_RE = re.compile(r"^\|?[\s:|-]+\|?$")
# 머리글 끝의 "(억 원)" 같은 괄호 — 계열 이름에서 떼어 단위 기본값으로 올린다.
_TRAILING_PAREN_RE = re.compile(r"[（(]\s*(?:단위\s*[:：]\s*)?([^）)]+)\s*[）)]\s*$")
# 제목 꼬리의 단위 — "제목 (단위: %)"에서 떼어 단위 칸으로 옮긴다(HWPX 조립의
# _CAPTION_TRAILING_UNIT_RE와 같은 규칙). 안 떼면 그림 캡션에 단위가 박혀 표 관례와 어긋난다.
_CAPTION_UNIT_RE = re.compile(r"\s*[（(]\s*단위\s*[:：]\s*([^)）]*?)\s*[）)]\s*$")
# 숫자 하나 — 천 단위 콤마와 소수점, 앞 부호를 받는다(charts._NUMBER_RE와 같은 식).
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# 음수를 나타내는 앞 기호. 한글 보고서는 마이너스를 '△'로 적는다(정부·한국은행 표기 관례).
# 이것을 안 읽으면 "수입액 △934"가 +934로 그려진다 — 감소가 증가로 뒤집힌 그래프가
# 값 라벨까지 달고 나간다(v7 3.2절 실측, 2026-08-28).
_NEGATIVE_SIGNS = ("△", "▽", "−", "–", "-")
# 방향 화살표는 부호가 아니다 — '▲'는 문서마다 증가를 뜻하기도, 값의 부호이기도 하다.
# 뜻이 갈리는 기호는 자동 변환에서 숫자로 읽지 않는다(사람이 보고 고르는 건 막지 않는다).
_AMBIGUOUS_SIGNS = ("▲", "▼")
# 값 칸으로 인정하는 꼴 — 부호 + 숫자 + **붙어 있는** 짧은 단위꼬리. 그게 전부여야 한다.
#
# 여기가 헐거우면 서술 열이 수치 열로 둔갑한다. "문자열에 든 첫 숫자"를 집던 규칙은
# v7 실측에서 셋을 한꺼번에 냈다(2026-08-28): 셀 끝 "(출처 7)"의 7을 값으로 읽고,
# "1단계 미인지"의 1을 값으로 읽고, "자료상 '23년 값만 제시"의 23을 값으로 읽었다.
# 숫자와 단위 사이 공백을 안 받는 것이 "1 미인지"와 "54.8%"를 가르는 유일한 구조다.
_NUMERIC_CELL_RE = re.compile(r"^[+\-−–△▽]?\s*\d[\d,]*(?:\.\d+)?[^\d\s]{0,4}$")
# 칸 안의 숫자를 전부 센다. 하나를 넘으면 첫 숫자만 집는 규칙이 값을 잘라 먹는다
# ("1조 96억 원" → 1, "3만 6,000명(10년)" → 3).
_NUMBERS_IN_CELL_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class MarkdownTable:
    """본문 블록에서 읽어 낸 마크다운 표 하나."""

    caption: str  # 표 위 제목 줄 — 번호를 뗀 문구. 없으면 빈 문자열.
    caption_unit: str  # 제목 꼬리에서 뗀 단위("%", "억 원")
    headers: list[str]
    rows: list[list[str]]
    source: list[int]  # 블록에 붙어 있던 (출처 n) 번호 — 차트로 바뀌어도 근거는 따라간다


@dataclass(frozen=True)
class ConvertChoice:
    """어떻게 그릴지 — 사람이 다이얼로그에서 고르는 것과 같은 내용."""

    type: str
    x_col: int
    series_cols: list[int]
    title: str
    unit: str


def strip_markers(cell: str) -> str:
    """셀에서 인용 마커를 걷는다 — "(출처 7)"의 7은 값이 아니다(table_check와 같은 관례)."""
    return MARK_RE.sub(" ", cell).strip()


def try_number(token: str) -> float | None:
    """숫자로 읽히면 숫자, 아니면 None. '△36'은 -36으로 읽는다."""
    match = _NUMBER_RE.search(token)
    if match is None:
        return None
    value = float(match.group().replace(",", ""))
    if token[: match.start()].strip().endswith(_NEGATIVE_SIGNS):
        return -abs(value)
    return value


def is_numeric_cell(cell: str) -> bool:
    """이 칸이 '값 하나'인가 — 숫자를 품은 서술문은 값이 아니다.

    try_number가 무엇이든 첫 숫자를 집어 주므로, 어느 열을 값 열로 삼을지는 이쪽이
    정해야 한다. 둘을 갈라 두지 않으면 서술 열이 조용히 계열이 된다.
    """
    text = strip_markers(cell)
    if any(sign in text for sign in _AMBIGUOUS_SIGNS):
        return False
    return _NUMERIC_CELL_RE.match(text) is not None


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.replace("**", "").strip() for c in stripped.split("|")]


def is_table_caption(block: str) -> bool:
    """이 블록이 표 제목 줄 하나인가 — 표 앞 문단으로 따로 사는 "표: 제목"을 알아본다."""
    text = _SOURCE_MARK_RE.sub("", block).strip()
    return "\n" not in text and _CAPTION_RE.match(text) is not None


def find_table(block: str) -> MarkdownTable | None:
    """블록 안의 마크다운 표를 읽는다. 표가 없거나 데이터 행이 없으면 None."""
    if "```" in block:
        return None  # 펜스 안(이미 차트)은 대상이 아니다
    lines = block.split("\n")
    start = next((i for i, line in enumerate(lines) if re.match(r"^\s*\|", line)), -1)
    if start < 0:
        return None

    table: list[list[str]] = []
    i = start
    while i < len(lines) and re.match(r"^\s*\|", lines[i]):
        if not _SEPARATOR_RE.match(lines[i].strip()):
            table.append(_cells(lines[i]))
        i += 1
    if len(table) < 3:
        return None  # 머리행 + 항목 2개 미만이면 그릴 게 없다

    before = " ".join(lines[:start]).strip()
    caption_match = _CAPTION_RE.match(_SOURCE_MARK_RE.sub("", before).strip())
    source: list[int] = []
    for m in _SOURCE_MARK_RE.finditer(block):
        for part in m.group(1).split(","):
            n = int(part.strip())
            if n not in source:
                source.append(n)

    headers, *rows = table
    # 머리행보다 짧은 행은 빈 칸으로 채운다 — 열 인덱스가 어긋나면 엉뚱한 값을 그린다.
    padded = [[r[i] if i < len(r) else "" for i in range(len(headers))] for r in rows]
    caption = caption_match.group(1).strip() if caption_match else ""
    caption_unit = ""
    unit_tail = _CAPTION_UNIT_RE.search(caption)
    if unit_tail is not None:
        caption_unit = unit_tail.group(1)
        caption = caption[: unit_tail.start()].strip()
    return MarkdownTable(
        caption=caption,
        caption_unit=caption_unit,
        headers=headers,
        rows=padded,
        source=source,
    )


def numeric_columns(table: MarkdownTable) -> list[int]:
    """값 열로 쓸 수 있는 열 번호 — 데이터 칸이 **전부** 값 하나로 읽히는 열.

    과반이 아니라 전부여야 한다. 한 칸이라도 값이 아니면 그 계열은 구멍이 나 못 그린다.
    """
    return [
        col
        for col in range(len(table.headers))
        if table.rows and all(is_numeric_cell(r[col]) for r in table.rows)
    ]


def split_unit(header: str) -> tuple[str, str]:
    """머리글에서 단위 후보를 뗀다 — "투자액(억 달러)" → ("투자액", "억 달러")."""
    m = _TRAILING_PAREN_RE.search(header)
    if m is None:
        return header.strip(), ""
    return header[: m.start()].strip() or header.strip(), m.group(1).strip()


def default_choice(table: MarkdownTable) -> ConvertChoice:
    """표에서 처음 열어 보일 기본 선택 — x축은 첫 비수치 열, 값은 **단위가 같은** 수치 열들."""
    numeric = numeric_columns(table)
    # x축을 먼저 확정한 뒤 값 후보에서 뺀다 — -1로 걸러 내면 x축 열이 값 계열에도 남는다.
    first_non_numeric = next((i for i in range(len(table.headers)) if i not in numeric), -1)
    x_col = 0 if first_non_numeric < 0 else first_non_numeric
    candidates = [c for c in numeric if c != x_col]
    # 값 열 필터는 머리글 단위끼리 비교한다 — 제목에서 뗀 단위는 표 전체의 것이라
    # 열 선별에는 못 쓰고, 머리글에 단위가 없을 때 표시용 기본값으로만 올린다.
    header_unit = split_unit(table.headers[candidates[0]])[1] if candidates else ""
    return ConvertChoice(
        type="bar",
        x_col=x_col,
        series_cols=[c for c in candidates if split_unit(table.headers[c])[1] == header_unit][
            :MAX_SERIES
        ],
        title=table.caption,
        unit=header_unit or table.caption_unit,
    )


def has_mixed_units(table: MarkdownTable, series_cols: list[int]) -> bool:
    """고른 값 열들의 단위가 섞여 있는가 — 한 축에 얹으면 작은 값이 눌린다."""
    units = [split_unit(table.headers[c])[1] for c in series_cols]
    return len(units) > 1 and any(u != units[0] for u in units)


def ambiguous_cell(table: MarkdownTable, x_col: int, series_cols: list[int]) -> str | None:
    """값이 하나로 안 읽히는 칸을 찾는다 — 없으면 None, 있으면 사람이 읽을 설명."""
    for row in table.rows:
        for col in series_cols:
            cell = strip_markers(row[col])
            if len(_NUMBERS_IN_CELL_RE.findall(cell)) > 1:
                return (
                    f"'{row[x_col]}' 행의 \"{cell}\"에서 {try_number(cell)}만 씁니다"
                    " — 한 칸에 숫자가 여럿이라 값이 잘립니다"
                )
    return None


def build_spec(table: MarkdownTable, choice: ConvertChoice, original_block: str) -> ChartSpec:
    """표 + 선택 → 차트 스펙. 원본 블록은 table에 담아 되돌리기·폴백에 쓴다."""
    # x축 이름이 빈 행은 통째로 뺀다 — x와 값을 따로 걸러 내면 개수가 어긋난다.
    rows = [r for r in table.rows if r[choice.x_col].strip() != ""]
    series = tuple(
        ChartSeries(
            name=split_unit(table.headers[col])[0] or f"계열 {col + 1}",
            values=tuple(try_number(strip_markers(r[col])) or 0.0 for r in rows),
        )
        for col in choice.series_cols
    )
    return ChartSpec(
        type=choice.type,
        title=choice.title.strip(),
        x=tuple(r[choice.x_col].strip() for r in rows),
        series=series,
        unit=choice.unit.strip(),
        source=tuple(table.source),
        table=original_block.strip(),
    )


# ── 판정층: 사람이 봐도 이견이 없을 표만 ──────────────────────────────────────

# 자동 변환이 다루는 x축 항목 수. 아래로는 그래프랄 것이 없고(2점짜리 막대는 표가 낫다),
# 위로는 가로축 라벨이 겹쳐 뭉갠다.
AUTO_MIN_POINTS = 3
AUTO_MAX_POINTS = 12
# x축 라벨 길이 상한. 이보다 길면 축이 서술문이라는 뜻이고(성숙도 표의 "무상할당 축소
# 일정 등 규제 확대 시나리오 반영"), 가로축에 얹으면 글자가 겹쳐 읽히지 않는다.
AUTO_MAX_LABEL_LEN = 20

# 시간 축으로 볼 x 라벨 — 네 자리 연도나 분기·월·반기 표기. "1|2|3" 같은 순번은 안 받는다
# (순번을 시간으로 읽으면 뜻 없는 꺾은선이 된다).
_TIME_POINT_RE = re.compile(
    r"^(?:(?:'\d{2}|\d{4})\s*(?:년도?)?"
    r"(?:\s*(?:상반기|하반기|[1-4]\s*(?:분기|Q)|\d{1,2}\s*월))?"
    r"|[1-4]\s*분기|\d{1,2}\s*월|상반기|하반기)$",
    re.I,
)

# 구성비 열임을 알리는 머리말 — qa/table_check._SHARE_HEADER_RE와 같은 눈금이다.
# 성장률·증감률 열을 원형으로 그리면 뜻이 없으므로 머리말로 좁힌다.
_SHARE_HEADER_RE = re.compile(r"비중|점유율|구성비|구성\s*비율|비율|share|portion|mix", re.I)
# 원형으로 볼 합계 범위 — 반올림 누적을 감안한다(table_check._SHARE_SUM_BAND와 같은 값).
_SHARE_SUM_BAND = (90.0, 110.0)
# 합계·소계 행은 조각이 아니라 답이다 — 원형에 넣으면 자기 자신이 절반을 먹는다.
_TOTAL_ROW_RE = re.compile(
    r"^(?:합\s*계|계|총\s*계|소\s*계|전\s*체|누\s*계|total|sum|subtotal)$|합산", re.I
)


@dataclass(frozen=True)
class AutoVerdict:
    """자동 판정 결과 — 바꿀 것이면 choice, 아니면 왜 안 바꾸는지."""

    choice: ConvertChoice | None
    reason: str  # 안 바꾼 사유(집계용 라벨). 바꿀 때는 고른 유형.


def _unit_tail(label: str) -> str:
    """라벨 꼬리 괄호가 '단위'로 읽히면 그 단위 — 아니면 빈 문자열.

    괄호 꼬리는 단위이기도 하고 한정어이기도 하다("(TWh)" vs "(아시아 지역 기준)").
    짧고 공백이 없는 것만 단위로 본다 — 한정어까지 단위로 세면 멀쩡한 표가 걸린다.
    """
    tail = split_unit(label)[1]
    return tail if 0 < len(tail) <= 6 and not re.search(r"\s", tail) else ""


def _row_units_mixed(labels: list[str]) -> bool:
    """x축 항목마다 단위가 다른가 — 전치된 표의 함정.

    "참여기업 수(개) 424 / 이행률(%) 53"을 한 축에 얹으면 424가 53을 눌러 뜻이 없다.
    열 머리글 단위만 보던 검사는 이 꼴을 통과시킨다 — 단위가 **행**에 있기 때문이다
    (v7 3.4·4.1절 실측, 2026-08-28).
    """
    units = {_unit_tail(label) for label in labels}
    return len(units) > 1 and any(u for u in units)


def _is_index_series(table: MarkdownTable, col: int) -> bool:
    """이 열이 1부터 이어지는 순번인가 — 등급·번호 열은 값이 아니라 줄 이름이다."""
    values = [try_number(strip_markers(r[col])) for r in table.rows]
    return all(v is not None for v in values) and [v for v in values if v is not None] == [
        float(i + 1) for i in range(len(values))
    ]


def _is_time_axis(labels: list[str]) -> bool:
    return all(_TIME_POINT_RE.match(label.strip()) is not None for label in labels)


def auto_choice(table: MarkdownTable) -> AutoVerdict:
    """사람 없이 바꿔도 되는 표인가 — 아니면 사유를 달아 표로 남긴다.

    거르는 순서는 값이 상하는 것부터다(전사 손실 → 뜻 없는 그림 → 못 그림).
    """
    if not table.caption.strip():
        # 제목이 없으면 <그림 N-M> 캡션이 빈 채로 나가고 그림 목차에도 빈 줄이 선다.
        return AutoVerdict(None, "제목 없음")

    choice = default_choice(table)
    numeric = [c for c in numeric_columns(table) if c != choice.x_col]
    if not choice.series_cols:
        return AutoVerdict(None, "값 열 없음")
    if len(numeric) > len(choice.series_cols):
        # 단위가 다른 수치 열이 남아 있다 — 그걸 뺀 그래프는 표가 말하던 것의 일부만
        # 말한다. 사람이 무엇을 보일지 고르는 게 맞다.
        return AutoVerdict(None, "단위 혼합")
    if len(numeric) > MAX_SERIES:
        # default_choice가 앞에서 잘라 버리므로 여기서 막지 않으면 열이 조용히 사라진다.
        return AutoVerdict(None, "계열 상한 초과")

    problem = ambiguous_cell(table, choice.x_col, choice.series_cols)
    if problem is not None:
        return AutoVerdict(None, "한 칸 다중 숫자")

    labels = [r[choice.x_col].strip() for r in table.rows if r[choice.x_col].strip()]
    if len(labels) != len(table.rows):
        return AutoVerdict(None, "빈 x축 라벨")
    if len(set(labels)) != len(labels):
        # 같은 라벨이 두 번 서면 어느 막대가 어느 행인지 못 읽는다.
        return AutoVerdict(None, "x축 라벨 중복")
    if len(labels) < AUTO_MIN_POINTS:
        return AutoVerdict(None, "항목 부족")
    if len(labels) > AUTO_MAX_POINTS:
        return AutoVerdict(None, "항목 과다")
    if any(len(label) > AUTO_MAX_LABEL_LEN for label in labels):
        return AutoVerdict(None, "x축 라벨 서술형")
    if _row_units_mixed(labels):
        return AutoVerdict(None, "행 단위 혼합")
    if any(_is_index_series(table, col) for col in choice.series_cols):
        # 1·2·3… 순번 열은 지표가 아니라 줄 번호다. 막대로 그리면 계단만 나온다.
        return AutoVerdict(None, "순번 열")

    values = [
        try_number(strip_markers(r[c])) or 0.0 for c in choice.series_cols for r in table.rows
    ]
    if len(set(values)) == 1:
        # 전부 같은 값이면 그래프가 말해 줄 게 없다(0으로만 찬 표 포함).
        return AutoVerdict(None, "값 변화 없음")

    # ── 원형: 구성비 열 하나가 전체를 나눠 갖는 표.
    if len(choice.series_cols) == 1:
        col = choice.series_cols[0]
        header_name, header_unit = split_unit(table.headers[col])
        is_share = _SHARE_HEADER_RE.search(header_name) is not None
        unit = header_unit or table.caption_unit
        col_values = [try_number(strip_markers(r[col])) or 0.0 for r in table.rows]
        has_total_row = any(_TOTAL_ROW_RE.match(r[choice.x_col].strip()) for r in table.rows)
        if (
            is_share
            and unit.strip() in {"%", "％"}
            and not has_total_row
            and all(v >= 0 for v in col_values)
            and _SHARE_SUM_BAND[0] <= sum(col_values) <= _SHARE_SUM_BAND[1]
            and len(labels) <= MAX_SERIES
        ):
            return AutoVerdict(
                ConvertChoice(
                    type="pie",
                    x_col=choice.x_col,
                    series_cols=choice.series_cols,
                    title=choice.title,
                    unit=unit,
                ),
                "pie",
            )

    # ── 꺾은선: x축이 시간이면 추이다.
    if _is_time_axis(labels):
        return AutoVerdict(
            ConvertChoice(
                type="line",
                x_col=choice.x_col,
                series_cols=choice.series_cols,
                title=choice.title,
                unit=choice.unit,
            ),
            "line",
        )

    # ── 막대: 항목 대소 비교. x축이 수치면(연도가 아닌 숫자 라벨) 뜻이 흐리니 남긴다.
    if all(try_number(label) is not None for label in labels):
        return AutoVerdict(None, "수치 x축")
    return AutoVerdict(choice, "bar")


def block_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """본문 줄 → 블록 줄 범위. preview.tsx splitBlocks와 같은 경계다.

    펜스는 빈 줄이 들어 있어도 한 블록, 표 제목 줄은 표와 한 블록, 인용 마커만 있는
    문단은 앞 블록에 붙는다. 세 규칙이 어긋나면 화면이 자르는 덩어리와 서버가 바꾸는
    덩어리가 달라져, 사람이 되돌린 자리에 서버가 다시 그래프를 세운다.
    """
    ranges: list[tuple[int, int]] = []
    start = -1
    in_fence = False
    for i, line in enumerate(lines):
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
        if not in_fence and line.strip() == "":
            if start >= 0:
                ranges.append((start, i))
            start = -1
            continue
        if start < 0:
            start = i
    if start >= 0:
        ranges.append((start, len(lines)))

    merged: list[list[int]] = []
    for a, b in ranges:
        if merged:
            prev_text = "\n".join(lines[merged[-1][0] : merged[-1][1]])
            if is_table_caption(prev_text) and re.match(r"^\s*\|", lines[a]):
                merged[-1][1] = b  # 제목 줄부터 표 끝까지 한 덩어리로
                continue
            if _MARKER_ONLY_BLOCK_RE.match("\n".join(lines[a:b])):
                merged[-1][1] = b  # 출처 표기 단독 문단은 앞 블록의 일부다
                continue
        merged.append([a, b])
    return [(a, b) for a, b in merged]


# 인용 마커만 있는 문단 — preview.tsx MARKER_ONLY_BLOCK_RE와 같은 규칙.
_MARKER_ONLY_BLOCK_RE = re.compile(
    r"^[※*\-–ㅇ○◦\s]*(?:(?:출처|자료|참고)\s*[:：])?(?:\s|\(출처\s*[\d,\s]+\)|\[\d+\])+$"
)


@dataclass(frozen=True)
class ConversionReport:
    """한 절을 훑은 결과 — 바꾼 것과 안 바꾼 사유. 실측·로그용."""

    content: str
    converted: list[str]  # 바꾼 표의 제목
    types: list[str]  # 바꾼 차트 유형(converted와 같은 순서)
    skipped: list[str]  # 안 바꾼 사유 라벨


def convert_tables_to_charts(content: str) -> ConversionReport:
    """절 본문의 적합한 표를 차트 펜스로 바꾼다 — 값은 표 셀 그대로.

    바꾸지 않은 표는 손대지 않는다. 되돌리기·폴백을 위해 원본 블록은 펜스 안에 통째로
    남으므로, 이 함수가 지운 내용은 없다.
    """
    lines = content.split("\n")
    out: list[str] = []
    converted: list[str] = []
    types: list[str] = []
    skipped: list[str] = []
    prev_end = 0

    for a, b in block_ranges(lines):
        out.extend(lines[prev_end:a])  # 블록 사이 빈 줄은 그대로 둔다
        prev_end = b
        block = "\n".join(lines[a:b])
        table = find_table(block)
        if table is None:
            out.extend(lines[a:b])
            continue
        verdict = auto_choice(table)
        if verdict.choice is None:
            skipped.append(verdict.reason)
            out.extend(lines[a:b])
            continue
        spec = build_spec(table, verdict.choice, block)
        out.extend(to_fence(spec).split("\n"))
        converted.append(spec.title)
        types.append(verdict.reason)

    out.extend(lines[prev_end:])
    return ConversionReport(
        content="\n".join(out), converted=converted, types=types, skipped=skipped
    )
