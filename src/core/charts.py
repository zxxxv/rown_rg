"""본문 속 차트 블록 규약 — 스펙 문법과 파싱을 한곳에서 정의한다.

차트는 본문 마크다운 **문자열 안에** 코드펜스로 산다:

    ```chart
    type: bar
    title: 주요국 SMR 개발 현황
    unit: 억 달러
    x: 미국 | 중국 | 한국
    series: 투자액 = 120 | 95 | 30
    source: 3, 7
    table: |
      | 국가 | 투자액 |
      |------|--------|
      | 미국 | 120 |
    ```

문자열인 이유가 있다. 편집·AI 재작성·저장·내보내기가 전부 마크다운 문자열 위에서
돌기 때문에(블록 = 빈 줄로 자른 부분 문자열, 저장 = 문자열 치환), 차트만 별도 테이블에
두면 그 배관을 전부 다시 짜야 한다. 펜스로 두면 기존 경로가 그대로 흐른다.

``table``에는 변환 전 원본 표를 그대로 담는다 — 사람이 "표로 되돌리기"를 누를 때
쓰고, 차트를 못 그리는 상황에서도 내용이 유실되지 않게 하는 안전망이다.

파싱은 관대하되 **검증은 엄격하다**: 계열 값 개수가 x축과 어긋나거나 숫자가 아니면
차트로 만들지 않는다. 틀린 그래프는 자리표시자보다 나쁘다.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field

# 지원 차트 종류 — 보고서에서 실제로 쓰이는 형태로 한정한다.
# bar=항목 대소 비교, line=시간에 따른 변화, pie=구성비(합이 전체).
CHART_TYPES: frozenset[str] = frozenset({"bar", "line", "pie"})

# 계열 색 — 웹 SVG와 한글 PNG가 같은 색을 쓰도록 여기서 한 번만 정한다(웹은 이 값을 그대로 미러).
# 순서 고정이며 돌려쓰지 않는다: 계열이 늘어도 6번째 색을 지어내지 않고 상한에서 막는다.
# 검증 통과(밝은 배경 #fcfcfb): 인접쌍 색각이상 ΔE 9.1, 정상시야 ΔE 19.6.
# 3·4·5번은 배경 대비가 3:1 미만이라 완화 규칙이 걸린다 — 값 라벨을 항상 찍고 원본 표를
# spec.table에 남기는 것으로 충족한다(색만으로 구분하게 두지 않는다).
SERIES_COLORS: tuple[str, ...] = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")
MAX_SERIES = len(SERIES_COLORS)

# 계열 색 위에 얹는 글자색 두 벌. 어느 쪽을 쓸지는 대비로 고른다(label_ink).
# 색과 함께 여기 두는 이유는 두 렌더러가 같은 답을 내야 하기 때문이다 — 짙은 색이 서로
# 다르면 같은 조각을 웹은 흰 글자로, 한글 파일은 검은 글자로 그린다(실제로 어긋났다).
LABEL_INK_DARK = "#2d2d2a"
LABEL_INK_LIGHT = "white"


def _relative_luminance(hex_color: str) -> float:
    """WCAG 상대 휘도 — 대비 계산의 재료."""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def label_ink(hex_color: str) -> str:
    """이 색 위에 얹을 글자색 — 흰색과 짙은 색 중 대비가 높은 쪽.

    밝기 임계로 가르면 노랑(#eda100)처럼 경계에 걸친 색에서 대비 2.2:1짜리 흰 글자가
    남는다 — 눈으로 보면 안 읽힌다(2026-08-11 렌더 확인). 실제 대비율을 재서 고르면
    색이 늘어도 규칙이 그대로 선다. 웹은 ChartBlock.labelInk가 같은 식을 쓴다.
    """
    luminance = _relative_luminance(hex_color)
    on_light = 1.05 / (luminance + 0.05)
    on_dark = (luminance + 0.05) / (_relative_luminance(LABEL_INK_DARK) + 0.05)
    return LABEL_INK_LIGHT if on_light >= on_dark else LABEL_INK_DARK


# 본문 속 차트 펜스. 여는 줄의 언어 태그가 'chart'인 블록만 잡는다.
CHART_FENCE_RE = re.compile(r"^```chart[ \t]*\n(?P<body>.*?)^```[ \t]*$", re.M | re.S)

# "key: value" 한 줄. 값이 여러 줄인 필드(table)는 '|' 뒤 들여쓴 블록으로 이어진다.
_FIELD_RE = re.compile(r"^(?P<key>[a-z_]+)[ \t]*:[ \t]*(?P<value>.*)$")
_SERIES_RE = re.compile(r"^(?P<name>.+?)[ \t]*=[ \t]*(?P<values>.+)$")
# 숫자 — 천 단위 콤마와 소수점, 앞 부호를 받는다. 단위·기호가 붙어 있어도 숫자만 뽑는다.
# table_to_chart도 이 정의를 import한다(2026-09-03 통일 - "같은 식" 주석 복제이던 것).
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# 음수를 나타내는 앞 기호. 한글 보고서는 마이너스를 '△'로 적는다(정부·한국은행 표기
# 관례). 이것을 안 읽으면 "수입액 △934"가 +934로 그려진다 — 감소가 증가로 뒤집힌
# 그래프가 값 라벨까지 달고 나간다(v7 3.2절 실측, 2026-08-28). 그 수리가 변환기
# (table_to_chart)에만 있고 이 파서엔 없어, 수동 편집·모델 생성 펜스의 △값은 여전히
# 양수로 그려졌다 — 부호 해석을 여기 한 곳으로 모은다(2026-09-03).
NEGATIVE_SIGNS = ("△", "▽", "−", "–", "-")


def signed_number(token: str) -> float | None:
    """숫자로 읽히면 숫자, 아니면 None. '△36'은 -36으로 읽는다."""
    match = NUMBER_RE.search(token)
    if match is None:
        return None
    value = float(match.group().replace(",", ""))
    if token[: match.start()].strip().endswith(NEGATIVE_SIGNS):
        return -abs(value)
    return value


class ChartSpecError(ValueError):
    """차트 스펙이 그릴 수 없는 상태 — 호출부는 원본 표로 되돌리거나 건너뛴다."""


@dataclass(frozen=True)
class ChartSeries:
    """계열 하나 — 이름과 x축과 같은 길이의 값들."""

    name: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class ChartSpec:
    """본문에 그려질 차트 하나.

    ``source``는 원본 표가 달고 있던 출처 번호다 — 표를 차트로 바꿔도 근거는 따라간다.
    ``table``은 변환 전 원본 표(마크다운)이며 되돌리기용이다.
    """

    type: str
    title: str
    x: tuple[str, ...]
    series: tuple[ChartSeries, ...]
    unit: str = ""
    source: tuple[int, ...] = ()
    table: str = ""
    caption: str = field(default="", compare=False)  # 조립 시 붙는 "[그림 N-M] …"


def _split_list(value: str) -> list[str]:
    """'|'로 나눈 항목 목록 — 빈 항목은 버린다.

    콤마를 쓰지 않는 이유: 한글 보고서의 수치는 "3,200억 원"처럼 천 단위 콤마를 달고
    산다. 콤마를 구분자로 삼으면 값 하나가 둘로 쪼개져 조용히 틀린 그래프가 된다.
    '|'는 숫자에 등장하지 않고 마크다운 표의 칸 구분과도 같은 기호다.
    """
    return [part.strip() for part in value.split("|") if part.strip()]


def _split_numbers(value: str) -> list[str]:
    """콤마로 나눈 정수 목록 — 출처 번호처럼 천 단위 콤마가 없는 값에만 쓴다."""
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_number(token: str) -> float:
    """숫자 토큰 하나 → float. 단위·기호가 섞여 있어도 숫자 부분만 읽고 △는 음수다."""
    value = signed_number(token)
    if value is None:
        raise ChartSpecError(f"숫자로 읽을 수 없는 값: {token!r}")
    return value


def _parse_fields(body: str) -> tuple[dict[str, str], list[str]]:
    """펜스 본문 → (단일값 필드, series 줄들). 'table:'은 여러 줄을 통째로 받는다."""
    fields: dict[str, str] = {}
    series_lines: list[str] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _FIELD_RE.match(line.strip())
        if match is None:
            i += 1
            continue
        key, value = match.group("key"), match.group("value").strip()
        if value == "|":
            # 여러 줄 블록 — 들여쓴 줄이 이어지는 동안 모은다(원본 표 보관용).
            block: list[str] = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
                block.append(lines[i].strip())
                i += 1
            fields[key] = "\n".join(block).strip()
            continue
        if key == "series":
            series_lines.append(value)
        else:
            fields[key] = value
        i += 1
    return fields, series_lines


def parse_chart_spec(body: str) -> ChartSpec:
    """차트 펜스 본문 → ChartSpec. 그릴 수 없으면 ChartSpecError."""
    fields, series_lines = _parse_fields(body)

    chart_type = fields.get("type", "").strip().lower()
    if chart_type not in CHART_TYPES:
        raise ChartSpecError(f"지원하지 않는 차트 종류: {chart_type!r} (bar·line·pie만 가능)")

    x = tuple(_split_list(fields.get("x", "")))
    if len(x) < 2:
        raise ChartSpecError("x축 항목이 2개 미만이라 그릴 수 없음")

    series: list[ChartSeries] = []
    for raw in series_lines:
        match = _SERIES_RE.match(raw)
        if match is None:
            raise ChartSpecError(f"계열 형식이 '이름 = 값 | 값'이 아님: {raw!r}")
        values = tuple(_parse_number(v) for v in _split_list(match.group("values")))
        if len(values) != len(x):
            raise ChartSpecError(
                f"계열 '{match.group('name').strip()}'의 값 {len(values)}개가"
                f" x축 {len(x)}개와 맞지 않음"
            )
        series.append(ChartSeries(name=match.group("name").strip(), values=values))
    if not series:
        raise ChartSpecError("계열이 없어 그릴 수 없음")
    if len(series) > MAX_SERIES:
        # 색을 돌려쓰면 서로 다른 계열이 같은 색이 된다 — 지어내느니 막는다.
        raise ChartSpecError(f"계열이 {len(series)}개로 상한 {MAX_SERIES}개를 넘음")
    if chart_type == "pie":
        if len(series) != 1:
            raise ChartSpecError("원형 차트는 계열이 하나여야 함(구성비는 한 벌만 나타낸다)")
        if len(x) > MAX_SERIES:
            # 원형은 x축 항목 하나가 색 하나다 — 막대·꺾은선의 계열 상한이 여기선 조각 상한이다.
            # 넘으면 색이 돌아 서로 다른 조각이 같은 색이 된다.
            raise ChartSpecError(f"원형 차트 조각이 {len(x)}개로 상한 {MAX_SERIES}개를 넘음")

    source: list[int] = []
    for token in _split_numbers(fields.get("source", "")):
        if token.isdigit():
            source.append(int(token))

    return ChartSpec(
        type=chart_type,
        title=fields.get("title", "").strip(),
        x=x,
        series=tuple(series),
        unit=fields.get("unit", "").strip(),
        source=tuple(source),
        table=fields.get("table", ""),
    )


def has_chart_fence(md: str) -> bool:
    """본문 조각에 차트 펜스가 들어 있는가 — AI 재작성에서 걸러 내기 위한 판정."""
    return CHART_FENCE_RE.search(md) is not None


# 펜스 안에 보관된 원본 표 — 'table: |' 뒤 들여쓴 블록(report.py·chartSpec.ts와 같은 규칙).
_FENCE_TABLE_RE = re.compile(r"^table:[ \t]*\|[ \t]*\n(?P<table>(?:[ \t]+.*\n?)+)", re.M)


def chart_fences_as_tables(md: str) -> str:
    """차트 펜스를 그 안에 보관된 **원본 표**로 되돌린 사본 — 검사망이 읽을 형태.

    검사망(사실 대장·수치 대조)은 차트를 몰라도 된다. 오히려 알면 해롭다: 펜스의
    ``series: 조달 비중 = 50 | 59`` 같은 스펙 줄을 '명시된 사실'로 읽어 metric 이름이
    그대로 'series'인 엔트리를 적립했다(v7 8개 절 전부, 2026-08-28 실측).

    표를 차트로 바꾼 것은 **표현**이지 내용이 아니므로, 검사망에는 바꾸기 전 모습을
    보여 준다. 이 한 겹 덕에 검출기 15종 중 어느 것도 변환 전후로 답이 달라지지 않는다.
    """

    def _sub(match: re.Match[str]) -> str:
        table = _FENCE_TABLE_RE.search(match.group("body"))
        return textwrap.dedent(table.group("table")).strip() if table else ""

    return CHART_FENCE_RE.sub(_sub, md)


def to_fence(spec: ChartSpec) -> str:
    """ChartSpec → 본문에 넣을 차트 펜스 문자열(변환 UI가 저장할 형태)."""
    lines = [f"type: {spec.type}", f"title: {spec.title}"]
    if spec.unit:
        lines.append(f"unit: {spec.unit}")
    lines.append("x: " + " | ".join(spec.x))
    for s in spec.series:
        values = " | ".join(f"{v:g}" for v in s.values)
        lines.append(f"series: {s.name} = {values}")
    if spec.source:
        lines.append("source: " + ", ".join(str(n) for n in spec.source))
    if spec.table:
        lines.append("table: |")
        lines.extend(f"  {line}" for line in spec.table.splitlines())
    return "```chart\n" + "\n".join(lines) + "\n```"
