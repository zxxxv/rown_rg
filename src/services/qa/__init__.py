"""QA 서비스 — AI 후보의 합격/불합격을 코드로 판정 (LLM-judge 없음).

- gate: 순수 결정적 정적검사. HARD 실패는 후보 제외, SOFT 실패는 사람에게 경고.
  최종 선택은 사람 몫(QA_SELECT 게이트) — 여기서는 판정만 한다.
"""

from src.services.qa.gate import (
    check_bounds,
    check_citation_resolves,
    check_complete,
    check_numeric_grounded,
    check_renderable,
    check_structure_complete,
    gate_candidates,
    run_section_gate,
)

__all__ = [
    "check_bounds",
    "check_citation_resolves",
    "check_complete",
    "check_numeric_grounded",
    "check_renderable",
    "check_structure_complete",
    "gate_candidates",
    "run_section_gate",
]
