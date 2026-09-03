"""설계 브리프 — 수집 전에 사람이 확인하는 절별 실행 계획.

여태 사용자는 목차를 확정한 뒤 결과가 나올 때까지 **무엇이 실제로 검색되는지** 볼 수
없었다. 2026-08-14 탄소규제 런 분석에서 그 대가가 드러났다: 규제 4종을 같은 5절 틀로
비교하는 정석적인 목차였는데, 절 제목이 장마다 반복되니 12개 절이 글자까지 같은 검색
질의를 던져 네 장이 같은 자료를 인용했다(1.3과 2.3의 인용 자료 집합 완전 일치, 2장이
EU CBAM 장인데 RE100 자료를 씀). 목차가 잘못된 게 아니라, 목차가 질의로 번역되는 과정이
화면에 없었다.

그래서 이 브리프의 핵심은 **실제로 나갈 질의를 그대로 보여주는 것**이다.
``search_query``는 설명용 근사치가 아니라 검색기가 쓰는 바로 그 함수
(:func:`services.retrieval.section.section_search_query`)의 반환값이다 — 화면과 실행이
갈라지면 이 게이트는 안심시키는 장식이 된다.

판정은 전부 결정적이다(LLM 없음). 중복 질의는 문자열 비교로 끝나고, 사람이 고칠 대상도
명확하다. "있는 걸 검토시키기"보다 "겹치는 걸 지적하기"가 잘 걸린다는 원칙은 백로그의
누락 가드와 같다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.core.outline import CHARS_PER_PAGE
from src.core.types import SectionPlan
from src.prompts import AnalystSpec
from src.services.generation.writer_context import build_writer_context
from src.services.qa.gate import DEFAULT_MAX_CHARS, DEFAULT_MIN_CHARS
from src.services.research.web_research import collection_topic
from src.services.retrieval.section import section_search_query


def _volume(section: SectionPlan, catalog: dict[str, AnalystSpec] | None) -> dict[str, int] | None:
    """배정된 에이전트에서 나오는 목표 분량. 배정이 없으면 None(게이트 기본값).

    catalog는 개인 에이전트 포함 병합 카탈로그(stages._analyst_catalog) — 없이 부르면
    파일 카탈로그만 보므로 개인 에이전트 절의 분량이 전부 '목표 없음'으로 보인다.
    실제 작성은 카탈로그를 주입받아 분량이 적용되니, 브리프만 실행과 다르게 보이는
    거짓 화면이 된다(2026-08-14 탄소규제 목차 렌더에서 실측).
    """
    ctx = build_writer_context(section, catalog)
    if ctx.min_chars is None or ctx.max_chars is None:
        return None
    return {"min_chars": ctx.min_chars, "max_chars": ctx.max_chars}


def duplicate_query_groups(plan: list[SectionPlan]) -> list[dict[str, Any]]:
    """같은 검색 질의를 쓰는 절 묶음 — 2개 이상일 때만.

    검색은 결정적이라 같은 질의는 같은 후보 풀을 돌려준다. 즉 이 묶음의 절들은
    **반드시** 같은 근거로 쓰이고, 장이 달라도 구별되지 않는 글이 나온다.
    """
    groups: dict[str, list[SectionPlan]] = defaultdict(list)
    for section in plan:
        groups[section_search_query(section)].append(section)
    return [
        {
            "query": query,
            "sections": [
                {
                    "chapter_number": s.chapter_number,
                    "section_number": s.section_number,
                    "label": f"{s.chapter_number}.{s.section_number} {s.title}",
                }
                for s in sections
            ],
        }
        for query, sections in groups.items()
        if len(sections) > 1
    ]


# 페이지 환산 계수는 core.outline.CHARS_PER_PAGE가 단일 진실(2026-09-03 통일).
_CHARS_PER_PAGE = CHARS_PER_PAGE

# 배정 없는 절의 분량 추정 폴백 = 정적 게이트 기본 경계 그 자체(재선언이던 것을
# import로 - 게이트 경계를 조정하면 비용 추정도 같이 움직인다, 2026-09-03 통일).
_FALLBACK_MIN_CHARS = DEFAULT_MIN_CHARS
_FALLBACK_MAX_CHARS = DEFAULT_MAX_CHARS

# 모드별 절당 실측 단가(USD, 수집·작성·검증 합산 근사) — 시작 전에 '얼마인지'를
# 보여주기 위한 범위 추정이지 청구 예측이 아니다. 근거 실측:
# - premium: 탄소규제 고급 런 $34/20절(2026-08-13) → 절감안 ①②(effort low·수집 Haiku)
#   적용 후 $18~20 전망 = 절당 0.9~1.7
# - standard: 절당 작성비 실측 $0.09~0.28(Sonnet, 2026-08-05) + 수집·검증 몫
# - economy: 검증 로드맵 복제 런 $2.81/34절(Haiku 분할, 2026-08-11) ≈ 절당 0.08
# 값이 낡으면 여기만 고친다(모드 라벨은 프론트 MODEL_MODE_LABEL과 대응).
_COST_PER_SECTION_USD: dict[str, tuple[float, float]] = {
    "economy": (0.08, 0.25),
    "standard": (0.20, 0.60),
    "premium": (0.90, 1.80),
}
_DEFAULT_MODE = "standard"

# 모드별 '런 1회 예상 비용' 고정값(USD) — 남은 한도 경고의 비교 기준(2026-08-15
# 사용자 지정: 고급 $30 / 표준 $20 / 절약 $15). 위의 절수 비례 범위와 별개로,
# 경고 문턱은 절 수에 흔들리지 않는 고정 기준을 쓴다 — "이 모드로 한 번 돌리면
# 대략 이만큼"이라는 운영 감각의 값이라 목차가 작아도 문턱이 무너지지 않는다.
# 부족해도 차단하지 않는다(경고만) — 강제는 quota_gate의 도달 시 차단 그대로.
EXPECTED_RUN_COST_USD: dict[str, float] = {
    "economy": 15.0,
    "standard": 20.0,
    "premium": 30.0,
}


def build_estimate(
    plan: list[SectionPlan],
    catalog: dict[str, AnalystSpec] | None = None,
    model_mode: str | None = None,
) -> dict[str, Any]:
    """총 분량·예상 비용 범위 — 비싼 런을 승인하기 전에 규모를 숫자로 보여준다.

    분량은 절별 volume_target 합(배정 없는 절은 게이트 기본 경계), 비용은 모드별
    실측 단가 × 절 수. 실행을 막는 값이 아니라 판단 재료다(차단은 quota_gate 몫).
    """
    total_min = total_max = 0
    for section in plan:
        volume = _volume(section, catalog)
        total_min += volume["min_chars"] if volume else _FALLBACK_MIN_CHARS
        total_max += volume["max_chars"] if volume else _FALLBACK_MAX_CHARS
    mode = model_mode if model_mode in _COST_PER_SECTION_USD else _DEFAULT_MODE
    low, high = _COST_PER_SECTION_USD[mode]
    n = len(plan)
    return {
        "model_mode": mode,
        "n_sections": n,
        "total_min_chars": total_min,
        "total_max_chars": total_max,
        "pages_min": max(1, total_min // _CHARS_PER_PAGE) if n else 0,
        "pages_max": max(1, total_max // _CHARS_PER_PAGE) if n else 0,
        "cost_usd_min": round(n * low, 1),
        "cost_usd_max": round(n * high, 1),
        # 남은 한도 경고의 비교 기준(고정값) — 프론트가 remaining_limit_usd와 비교한다.
        "expected_run_cost_usd": EXPECTED_RUN_COST_USD[mode],
    }


def collection_plan(plan: list[SectionPlan], topic: str) -> list[dict[str, Any]]:
    """장 단위 수집 계획 — 자료 수집은 장마다 한 콜씩 돈다.

    보고서 주제문이 실제로 일하는 곳이 여기뿐이라, 주제란에 무엇을 적어야 하는지
    판단하려면 이 질의를 봐야 한다. 문자열은 수집기와 같은 함수(collection_topic)로
    만든다 — 화면과 실행이 갈라지면 보여주는 의미가 없다.
    """
    chapters: dict[int, dict[str, Any]] = {}
    for section in plan:
        chapter = chapters.get(section.chapter_number)
        if chapter is None:
            # 장 제목이 비면 수집 질의도 'N장'으로 간다(stages._chapter_groups와 같은 폴백).
            title = section.chapter_title or f"{section.chapter_number}장"
            chapter = {
                "chapter_number": section.chapter_number,
                "title": section.chapter_title,
                "collection_query": collection_topic(topic, title),
                "section_titles": [],
            }
            chapters[section.chapter_number] = chapter
        chapter["section_titles"].append(section.title)
    return [chapters[n] for n in sorted(chapters)]


def build_design_brief(
    plan: list[SectionPlan],
    *,
    topic: str = "",
    catalog: dict[str, AnalystSpec] | None = None,
    model_mode: str | None = None,
    domain_context: str = "",
) -> dict[str, Any]:
    """게이트 payload로 나갈 브리프. 순수 함수 — DB도 LLM도 안 본다.

    DB가 필요한 재료(개인 에이전트 카탈로그)는 호출부(stages.plan_brief)가 미리
    해석해 넘긴다 — writer_context와 같은 주입 계약.
    """
    duplicates = duplicate_query_groups(plan)
    sections = [
        {
            "section_id": str(s.section_id),
            "chapter_number": s.chapter_number,
            "section_number": s.section_number,
            "chapter_title": s.chapter_title,
            "title": s.title,
            "direction": s.direction,
            "key_points": list(s.key_points),
            "analysts": list(s.analysts),
            "builds_on": list(s.builds_on),
            # 검색기가 실제로 쓰는 문자열. 화면과 실행이 같아야 이 게이트가 의미 있다.
            "search_query": section_search_query(s),
            "volume": _volume(s, catalog),
        }
        for s in plan
    ]
    n_dup_sections = sum(len(g["sections"]) for g in duplicates)
    return {
        "message": "자료를 모으기 전에 절별 설계를 확인하세요. 목차를 고치면 다시 계산됩니다.",
        "topic": topic,
        # 프리셋의 도메인 맥락(보고서 유형의 틀·실측 구조) — AI 계획(brief_ai)이 절별
        # 질의·자료 전략을 만들 때 참고한다. 플래너 전용이던 시절 죽은 필드였다
        # (2026-08-20 발견: verbatim 목차 경로에선 어디에도 안 갔다).
        "domain_context": domain_context,
        "estimate": build_estimate(plan, catalog, model_mode),
        "chapters": collection_plan(plan, topic),
        "sections": sections,
        "duplicate_queries": duplicates,
        "warnings": {
            # 사람이 봐야 할 단 하나의 숫자 — 0이 아니면 그만큼의 절이 같은 자료를 쓴다.
            "duplicate_query_sections": n_dup_sections,
            "sections_without_analyst": [
                f"{s.chapter_number}.{s.section_number} {s.title}" for s in plan if not s.analysts
            ],
        },
    }
