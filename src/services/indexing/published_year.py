"""자료 발간연도 추출 — 자료 시점 축의 재료 (결정적, LLM 없음).

'21~'22년 자료가 무연도 현재형으로 유통되는 것이 검증런 2회 연속의 주 감점 축이었다
(K-RE100 74개사·"전기요금 40% 저렴" 등). 연도를 색인 때 청크에 실어 두면 작성 주입
("○○년 발간 자료 기준" 명기 강제)·게이트 표시·통계가 전부 이 값 하나에서 나온다.

원칙: **발간연도 ≠ 데이터 기준연도**(2024년 발간 보고서가 2021년 통계를 인용한다).
여기서 뽑는 것은 발간연도까지다 — 확신 없는 값을 다느니 안 단다(미상은 미상대로
프롬프트가 처리). 우선순위: 명시 라벨(발행/발간) > 제목·파일명 > 본문 머리 날짜 표기.
"""

from __future__ import annotations

import re

from src.core.clock import now as clock_now

_MIN_YEAR = 1990

# 숫자 경계 가드 - 타임스탬프("1712575062122")의 부분열 오인 방지.
_LABELED_RE = re.compile(
    r"(?:발행일|발행|발간|출판|발표|Published)[^\n\d]{0,12}(?<!\d)((?:19|20)\d{2})(?!\d)"
)
# 년.월.일 — 일까지 있어야 본문 서술과 갈린다(2026-08-28 v7 실측: "RE100은 …
# 2014년 9월 UN 기후정상회의에서 도입"의 캠페인 출범연도가 발간연도로 저장돼
# 연도 병존 판정 재료를 오염시켰다). 월 1~12·일 1~31 범위 검사는 DOI 조각
# ("KSCCR.2025.16.2.267")이 날짜로 보이는 것을 막는다.
_KOREAN_FULL_DATE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*[.년]\s*(?:1[0-2]|0?[1-9])\s*[월.]\s*(?:3[01]|[12]\d|0?[1-9])(?!\d)"
)
# 년.월뿐인 표기("2024. 8. 발간사")는 표지 서두에서만 믿는다 — 본문 프로즈의
# "○○년 ○월"과 무늬가 같아 위치로만 가를 수 있다.
_KOREAN_YM_HEAD_CHARS = 300
_KOREAN_DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*[.년]\s*(?:1[0-2]|0?[1-9])\s*[월.]")
_MONTH_DATE_RE = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?<!\d)((?:19|20)\d{2})(?!\d)"
)
_FY_RE = re.compile(r"FY\s?(?<!\d)((?:19|20)\d{2})(?!\d)")
_BARE_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _max_year() -> int:
    # 발간연도 상한 = 내년 - "2030 전망" 같은 목표연도가 발간연도로 오인되지 않게.
    return clock_now().year + 1


def _plausible(year: int) -> int | None:
    return year if _MIN_YEAR <= year <= _max_year() else None


def _first(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text)
    return _plausible(int(m.group(1))) if m else None


def extract_published_year(title: str | None, text_head: str) -> int | None:
    """제목(파일명)과 본문 머리에서 발간연도를 뽑는다. 확신 없으면 None.

    본문 머리의 명시 라벨(발행/발간)이 최우선. 다음은 제목(파일명)의 연도 -
    "…_2023.10.pdf"류 파일명 연도는 대개 발간 시점이고, 여럿이면 **최신**을 쓴다
    ("FY 2024-25 … March 2026 SIGNED"의 발간은 2026이다). 마지막으로 본문 머리의
    날짜 표기. 본문의 맨 연도(bare year)는 안 쓴다 - 목표·통계연도와 섞여 오인이 많다.
    """
    head = text_head or ""
    year = _first(_LABELED_RE, head)
    if year:
        return year
    title_years = [
        y for m in _BARE_YEAR_RE.finditer(title or "") if (y := _plausible(int(m.group(1))))
    ]
    if title_years:
        return max(title_years)
    for pattern in (_KOREAN_FULL_DATE_RE, _MONTH_DATE_RE, _FY_RE):
        year = _first(pattern, head)
        if year:
            return year
    return _first(_KOREAN_DATE_RE, head[:_KOREAN_YM_HEAD_CHARS])


def year_from_page_age(page_age: str | None) -> int | None:
    """웹 수집이 준 page_age("2024-08-01"·"August 1, 2024"류)에서 연도만."""
    if not page_age:
        return None
    return _first(_BARE_YEAR_RE, page_age)
