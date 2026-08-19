"""SectionPlan 직렬화 — ``projects.config["_section_plan"]`` 정본.

plan은 오래 "projects 테이블에 자리가 없다"는 이유로 게이트 payload에만 실렸다. 그
결과 목차 표현이 세 벌로 갈라져 서로 다른 것을 알게 됐다:

======================  ==========  ==================================  =============
표현                    section_id  direction / key_points / analysts   chapter_title
======================  ==========  ==================================  =============
``config.outline``      없음        있음                                장 레벨에 있음
게이트 payload          있음        있음                                없음
``sections`` 행         있음        없음                                있음
======================  ==========  ==================================  =============

새는 지점이 이미 있었다(2026-08-14 운영 DB 실측). ``sections.chapter_title``을
``services/sections/store.py``가 ``load_preset()``에서 가져오는 바람에:

- 프리셋 없는 프로젝트(탄소규제 런)는 전부 ``'1장'~'4장'``으로 저장됐다 —
  ``config.outline``에는 ``'글로벌 RE100'·'EU CBAM'…``이 멀쩡히 있는데도.
- 프리셋을 쓰되 장을 더한 프로젝트는 6장에 프리셋 6번째 제목이 붙어 **틀린 제목**이
  저장됐다(폴백보다 나쁘다 — 그럴듯해서 안 보인다).

config는 제약 없는 JSONB라 자리는 처음부터 있었다. plan을 여기 정본으로 두면 게이트
payload 왕복·``_rehydrate_section_plan``·위치 대응 복원이 전부 파생으로 내려간다.

주의: JSONB를 in-place로 고치면 SQLAlchemy가 dirty로 표시하지 않아 커밋해도 안 써진다.
그래서 :func:`config_with_plan`은 **항상 새 dict를 돌려준다** — 호출부가 실수할 수 없게
API 모양으로 못 박은 것이다(선례: ``workflows/runner.py``의 ``models`` 스냅샷 재할당).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from src.core.types import SectionPlan

# config 안의 내부 키. 밑줄 접두는 "서버 소유, 사용자 옵션 아님" 표시다.
SECTION_PLAN_KEY = "_section_plan"


def dump_section_plan(plan: Sequence[SectionPlan]) -> list[dict[str, Any]]:
    """SectionPlan 목록 → JSON 직렬화 가능한 dict 목록."""
    return [
        {
            "section_id": str(s.section_id),
            "chapter_number": s.chapter_number,
            "section_number": s.section_number,
            "title": s.title,
            "chapter_title": s.chapter_title,
            "direction": s.direction,
            "key_points": list(s.key_points),
            "analysts": list(s.analysts),
            "search_queries": list(s.search_queries),
        }
        for s in plan
    ]


def load_section_plan(items: Any) -> list[SectionPlan]:
    """dict 목록 → SectionPlan 목록. 깨진 항목은 건너뛴다(복원이 실행을 죽이면 안 된다).

    구버전 payload에는 chapter_title이 없다 — .get 기본값으로 읽어 빈 문자열이 되고,
    그러면 검색·작성이 장 맥락 없이 돌던 옛 동작과 같아진다(조용한 열화, 크래시 아님).
    """
    plan: list[SectionPlan] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            plan.append(
                SectionPlan(
                    section_id=UUID(str(item["section_id"])),
                    chapter_number=int(item["chapter_number"]),
                    section_number=int(item["section_number"]),
                    title=str(item["title"]),
                    chapter_title=str(item.get("chapter_title") or ""),
                    direction=str(item.get("direction") or ""),
                    key_points=[str(k) for k in (item.get("key_points") or [])],
                    analysts=[str(a) for a in (item.get("analysts") or [])],
                    search_queries=[str(q) for q in (item.get("search_queries") or [])],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return plan


def plan_from_config(config: dict[str, Any] | None) -> list[SectionPlan]:
    """projects.config에 저장된 정본 plan. 없으면 빈 목록."""
    if not isinstance(config, dict):
        return []
    return load_section_plan(config.get(SECTION_PLAN_KEY))


def config_with_plan(config: dict[str, Any] | None, plan: Sequence[SectionPlan]) -> dict[str, Any]:
    """plan을 담은 **새** config dict. plan이 비면 키를 지운다(정본 없음 = 없음).

    반드시 반환값을 재할당할 것 — ``project.config = config_with_plan(...)``.
    in-place 수정은 SQLAlchemy가 감지하지 못한다(모듈 docstring 참조).
    """
    updated = dict(config or {})
    if plan:
        updated[SECTION_PLAN_KEY] = dump_section_plan(plan)
    else:
        updated.pop(SECTION_PLAN_KEY, None)
    return updated
