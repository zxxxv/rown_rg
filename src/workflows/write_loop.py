"""write 루프 오케스트레이션 — 섹션별 검색→생성→게이트, 그리고 QA_SELECT 전후 순수 헬퍼.

파이프라인 척추(pipeline.py)와 runner가 이 함수들을 호출한다. 의존성(검색·생성)은
주입식이라 실검색/실LLM 없이 단위 테스트된다.

무한성 불변식: 후보 N은 상수(1회 fan-out), 게이트는 결정적(종료 보장), 최종 선택은 사람.
"""

from __future__ import annotations

from uuid import UUID

from src.clients.llm.base import LLMClient
from src.core.state import ProjectState
from src.core.types import GateResult, SectionCandidateSet, SectionDraft
from src.services.generation.candidates import (
    DEFAULT_MODEL,
    DEFAULT_N,
    generate_section_candidates,
)
from src.services.qa.gate import check_structure_complete, gate_candidates
from src.services.retrieval.section import SectionRetriever


async def run_write_loop(
    state: ProjectState,
    *,
    retrieve: SectionRetriever,
    client: LLMClient | None = None,
    n: int = DEFAULT_N,
    model: str = DEFAULT_MODEL,
) -> ProjectState:
    """section_plan의 각 섹션을 검색→후보 생성→정적 게이트로 처리해 state에 적재.

    검색은 주입된 retrieve로, 생성은 generate_section_candidates(client 주입 가능)로.
    게이트 판정까지 마친 SectionCandidateSet들을 state.section_candidates에 넣어 돌려준다.
    """
    candidate_sets: list[SectionCandidateSet] = []
    for section in state.section_plan:
        chunks = await retrieve(section)
        drafts = await generate_section_candidates(
            section,
            chunks,
            n=n,
            model=model,
            client=client,
            user_id=state.user_id,
            project_id=state.project_id,
        )
        candidate_sets.append(gate_candidates(section.section_id, drafts, chunks))
    return state.with_section_candidates(candidate_sets)


def qa_select_payload(state: ProjectState) -> dict[str, object]:
    """QA_SELECT 게이트 payload — 섹션별 '살아남은'(HARD 통과) 후보 + soft 경고만 사람에게.

    HARD 실패 후보는 노출하지 않는다. survivors가 0이면 all_excluded=True로 표시 —
    사람이 재생성/수동편집을 결정할 신호.
    """
    sections: list[dict[str, object]] = []
    for cset in state.section_candidates:
        survivors: list[dict[str, object]] = [
            {
                "candidate_id": str(cand.candidate_id),
                "content": cand.draft.content,
                "cited_chunk_ids": [str(c) for c in cand.draft.cited_chunk_ids],
                "warnings": [{"check": w.check, "detail": w.detail} for w in cand.report.warnings],
            }
            for cand in cset.survivors
        ]
        sections.append(
            {
                "section_id": str(cset.section_id),
                "candidates": survivors,
                "all_excluded": not survivors,
            }
        )
    return {
        "message": "섹션별로 후보를 하나씩 고르세요. (정적검사 통과분만 표시)",
        "sections": sections,
    }


def apply_selection(state: ProjectState, selections: dict[str, str]) -> ProjectState:
    """사람 결정(section_id→candidate_id, JSON 문자열 UUID)을 state에 반영."""
    updated = state
    for section_id_str, candidate_id_str in selections.items():
        updated = updated.record_selection(UUID(section_id_str), UUID(candidate_id_str))
    return updated


def check_assembled(state: ProjectState) -> tuple[list[SectionDraft], GateResult]:
    """선택된 draft를 조립하고 보고서 레벨 정적검사(structure_complete)를 실행.

    누락 섹션이 있으면 GateResult.passed=False — 최종 게이트에서 사람에게 되돌릴 신호.
    """
    drafts = state.selected_drafts()
    result = check_structure_complete(drafts, state.section_plan)
    return drafts, result
