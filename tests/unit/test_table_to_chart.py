"""표 → 차트 자동 변환 규칙.

거르는 규칙이 본체이므로 "안 바꾼다" 쪽을 더 촘촘히 잰다. v7 실측(2026-08-28)에서
자동 변환을 처음 켰을 때 나온 결함들이 그대로 회귀 사례로 들어 있다 — 부호 소실·
출처 번호 오인·순번 열·서술 셀·행 단위 혼합 다섯이 전부 실제로 그려졌던 그림이다.
"""

from __future__ import annotations

import pytest

from src.core.charts import chart_fences_as_tables, parse_chart_spec
from src.core.table_to_chart import (
    auto_choice,
    convert_tables_to_charts,
    find_table,
    is_numeric_cell,
    try_number,
)


def _fence_spec(md: str):
    """변환 결과의 첫 펜스를 파서에 통과시켜 스펙으로 돌려준다(왕복 검증)."""
    from src.core.charts import CHART_FENCE_RE

    match = CHART_FENCE_RE.search(md)
    assert match is not None, "차트 펜스가 없다"
    return parse_chart_spec(match.group("body"))


def _table(md: str):
    table = find_table(md)
    assert table is not None
    return table


# ── 값 칸 판정 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cell",
    ["120", "4,027", "3.2%", "△36", "+160", "-934", "59", "0.02", "53%p"],
)
def test_값_칸으로_읽는_꼴(cell: str) -> None:
    assert is_numeric_cell(cell)


@pytest.mark.parametrize(
    "cell",
    [
        "정부 재정지원 가속(출처 1)",  # 셀 끝 출처 번호를 값으로 읽던 자리
        "1단계 미인지",  # 등급 열
        "1 미인지",
        "자료상 ’23년 값만 제시",  # 서술문 속 연도를 값으로 읽던 자리
        "미인지 54.8%",
        "1조 96억 원",
        "제도 인식 없음",
        "",
        "-",
        "▲12",  # 방향 화살표는 부호인지 증감인지 뜻이 갈린다
    ],
)
def test_값_칸이_아닌_꼴(cell: str) -> None:
    assert not is_numeric_cell(cell)


def test_한글_보고서의_음수는_세모다() -> None:
    assert try_number("△36") == -36.0
    assert try_number("△1,400") == -1400.0
    assert try_number("+160") == 160.0
    assert try_number("934") == 934.0


# ── 바꾸는 표 ─────────────────────────────────────────────────────────────────


def test_비교표는_막대가_된다() -> None:
    md = """표: 주요국 투자 현황
(단위: 억 달러)
| 국가 | 투자액 |
|---|---|
| 미국 | 120 |
| 중국 | 95 |
| 한국 | 30 |(출처 3, 7)
"""
    report = convert_tables_to_charts(md)
    assert report.types == ["bar"]
    spec = _fence_spec(report.content)
    assert spec.type == "bar"
    assert spec.title == "주요국 투자 현황"
    assert spec.unit == "억 달러"  # 제목 꼬리의 단위는 단위 칸으로 옮긴다
    assert spec.x == ("미국", "중국", "한국")
    assert spec.series[0].values == (120.0, 95.0, 30.0)
    assert spec.source == (3, 7)  # 표의 근거는 차트로 따라간다


def test_연도_축은_꺾은선이_된다() -> None:
    md = """표: 연도별 조달 비중
(단위: %)
| 연도 | PPA | 녹색요금제 |
|---|---|---|
| 2021 | 35 | 19 |
| 2022 | 31 | 24 |
| 2023 | 27 | 29 |
"""
    report = convert_tables_to_charts(md)
    assert report.types == ["line"]
    spec = _fence_spec(report.content)
    assert spec.x == ("2021", "2022", "2023")
    assert [s.name for s in spec.series] == ["PPA", "녹색요금제"]


def test_구성비는_원형이_된다() -> None:
    md = """표: 권역별 점유율
| 권역 | 비중(%) |
|---|---|
| 북미 | 40 |
| 유럽 | 30 |
| 아시아 | 25 |
| 기타 | 5 |
"""
    report = convert_tables_to_charts(md)
    assert report.types == ["pie"]
    spec = _fence_spec(report.content)
    assert spec.type == "pie"
    assert spec.unit == "%"


def test_감소는_음수로_그린다() -> None:
    """△를 못 읽으면 감소가 증가로 뒤집힌 그래프가 값 라벨까지 달고 나간다."""
    md = """표: 업종별 수입액 변화
(단위: million US$)
| 업종 | 수입액 변화 |
|---|---|
| 석유정제 | +160 |
| 철강 | △934 |
| 비철금속 | △966 |
"""
    spec = _fence_spec(convert_tables_to_charts(md).content)
    assert spec.series[0].values == (160.0, -934.0, -966.0)


def test_원본_표는_펜스_안에_남는다() -> None:
    md = """표: 주요국 투자 현황
| 국가 | 투자액 |
|---|---|
| 미국 | 120 |
| 중국 | 95 |
| 한국 | 30 |
"""
    report = convert_tables_to_charts(md)
    restored = chart_fences_as_tables(report.content)
    assert "| 미국 | 120 |" in restored
    assert "```chart" not in restored
    # 되돌린 본문에서 표를 다시 읽을 수 있어야 검사망이 바뀌기 전과 같은 것을 본다.
    assert find_table(restored) is not None


# ── 안 바꾸는 표 ──────────────────────────────────────────────────────────────


def _reason(md: str) -> str:
    verdict = auto_choice(_table(md))
    assert verdict.choice is None, f"바꾸면 안 되는 표를 바꿨다: {verdict.reason}"
    return verdict.reason


def test_서술_열은_출처_번호로_그리지_않는다() -> None:
    md = """표: 주요국 동향
| 구분 | 주요 동향 |
|---|---|
| EU | 수소환원제철 가속(출처 1) |
| 일본 | 시험설비 재정지원 확대(출처 1) |
| 한국 | 로드맵 추진(출처 7) |
"""
    assert _reason(md) == "값 열 없음"


def test_순번_열은_계열이_아니다() -> None:
    md = """표: 성숙도 등급
| 등급 | 점수 |
|---|---|
| 미인지 | 1 |
| 인지 | 2 |
| 선도 | 3 |
"""
    assert _reason(md) == "순번 열"


def test_행마다_단위가_다르면_한_축에_얹지_않는다() -> None:
    md = """표: 이행 실적 비교
| 구분 | 한국 |
|---|---|
| 참여기업 수(개) | 36 |
| 전력사용량(TWh) | 68 |
| 이행률(%) | 11.8 |
"""
    assert _reason(md) == "행 단위 혼합"


def test_열_단위가_섞이면_바꾸지_않는다() -> None:
    md = """표: 국가별 현황
| 국가 | 투자액(억 달러) | 기업 수(개) |
|---|---|---|
| 미국 | 120 | 40 |
| 중국 | 95 | 33 |
| 한국 | 30 | 12 |
"""
    assert _reason(md) == "단위 혼합"


def test_한_칸에_숫자가_여럿이면_바꾸지_않는다() -> None:
    md = """표: 사업비
| 항목 | 금액 |
|---|---|
| 총사업비 | 1조 96억 |
| 설계비 | 4,027억 |
| 공사비 | 5,000억 |
"""
    # "1조 96억"은 값 칸으로도 안 읽히므로 값 열 자체가 서지 않는다.
    assert _reason(md) in {"값 열 없음", "한 칸 다중 숫자"}


def test_제목이_없으면_바꾸지_않는다() -> None:
    md = """| 국가 | 값 |
|---|---|
| 미국 | 1 |
| 중국 | 2 |
| 한국 | 3 |
"""
    assert _reason(md) == "제목 없음"


def test_항목이_둘이면_바꾸지_않는다() -> None:
    md = """표: 두 항목
| 국가 | 값 |
|---|---|
| 미국 | 10 |
| 중국 | 20 |
"""
    assert _reason(md) == "항목 부족"


def test_x축이_서술문이면_바꾸지_않는다() -> None:
    md = """표: 단계별 정의
| 정의 | 값 |
|---|---|
| 자사 품목의 해당 여부를 아직 확인하지 못한 상태 | 10 |
| 대상 품목과 시행 시기를 인지한 상태 | 20 |
| 전환기간 보고 요청에 대응한 경험을 보유한 상태 | 30 |
"""
    assert _reason(md) == "x축 라벨 서술형"


def test_이미_차트인_블록은_건드리지_않는다() -> None:
    md = """```chart
type: bar
title: 이미 차트
x: 가 | 나
series: 값 = 1 | 2
```
"""
    report = convert_tables_to_charts(md)
    assert report.converted == []
    assert report.content == md


def test_바꾸지_않은_본문은_한_글자도_안_바뀐다() -> None:
    md = """## 1.1 개요

| 구분 | 내용 |
|---|---|
| 가 | 서술 |
| 나 | 서술 |

문단이다.(출처 3)
"""
    assert convert_tables_to_charts(md).content == md
