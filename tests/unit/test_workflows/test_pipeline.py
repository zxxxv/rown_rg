"""파이프라인 관통 검증 — write→QA_SELECT 정지→재수화+선택→assemble→완료 (인메모리).

stages의 검색기·LLM 전역을 fake로 교체해 실검색/실LLM/DB 없이 척추 배선을 검증한다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import ProjectStage, RetrievedChunk, ReviewGate, SectionPlan
from src.workflows.pipeline import Done, Paused, advance
from src.workflows.write_loop import apply_selection, rehydrate_from_payload


class _StubClient:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


@pytest.fixture
def fake_write(monkeypatch: pytest.MonkeyPatch) -> RetrievedChunk:
    """write 스테이지의 검색기·LLM을 fake로 교체. 인용 근거 chunk를 돌려준다."""
    chunk = RetrievedChunk(chunk_id=uuid4(), source_id=uuid4(), content="근거 " * 50, score=0.9)

    async def fake_retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        return [chunk]

    monkeypatch.setattr("src.workflows.stages._retriever_factory", lambda state: fake_retrieve)
    monkeypatch.setattr(
        "src.workflows.stages._write_client", _StubClient("이 섹션 본문입니다. [1] " * 30)
    )
    return chunk


def _state_at_research() -> ProjectState:
    return ProjectState(
        user_id=uuid4(),
        topic="주제",
        section_plan=[
            SectionPlan(chapter_number=1, section_number=1, title="개요"),
            SectionPlan(chapter_number=2, section_number=1, title="분석"),
        ],
        current_stage=ProjectStage.RESEARCHING,
    )


class TestWritePausesAtQaSelect:
    async def test_write_produces_candidates_and_pauses(self, fake_write: RetrievedChunk):
        outcome = await advance(_state_at_research())
        assert isinstance(outcome, Paused)
        assert outcome.review.gate is ReviewGate.QA_SELECT
        assert outcome.state.current_stage is ProjectStage.REVIEWING
        sections = outcome.review.payload["sections"]
        assert len(sections) == 2
        # 각 섹션에 survivors 존재 (게이트 통과)
        for sec in sections:
            assert len(sec["candidates"]) == 2
            assert sec["all_excluded"] is False


class TestResumeThroughAssemble:
    async def test_full_round_trip_completes(self, fake_write: RetrievedChunk):
        # 1) write → QA_SELECT 정지
        paused = await advance(_state_at_research())
        assert isinstance(paused, Paused)
        payload = paused.review.payload

        # 2) 사람이 각 섹션 첫 후보 선택 (decision)
        selections = {
            sec["section_id"]: sec["candidates"][0]["candidate_id"] for sec in payload["sections"]
        }

        # 3) resume — 새 프로세스처럼 빈 state에서 payload로 재수화 + 선택 반영
        fresh = ProjectState(
            user_id=paused.state.user_id, topic="주제", current_stage=ProjectStage.REVIEWING
        )
        fresh = rehydrate_from_payload(fresh, payload)
        fresh = apply_selection(fresh, selections)

        # 4) assemble → 완료
        done = await advance(fresh)
        assert isinstance(done, Done)
        assert done.state.current_stage is ProjectStage.COMPLETED
        assert len(done.state.selected_drafts()) == 2

    async def test_missing_selection_still_completes_but_incomplete_structure(
        self, fake_write: RetrievedChunk
    ):
        # 한 섹션만 선택 → assemble은 진행하되 selected_drafts는 1개 (structure 미완)
        paused = await advance(_state_at_research())
        assert isinstance(paused, Paused)
        payload = paused.review.payload
        first = payload["sections"][0]
        selections = {first["section_id"]: first["candidates"][0]["candidate_id"]}

        fresh = ProjectState(
            user_id=paused.state.user_id, topic="주제", current_stage=ProjectStage.REVIEWING
        )
        fresh = rehydrate_from_payload(fresh, payload)
        fresh = apply_selection(fresh, selections)
        done = await advance(fresh)
        assert isinstance(done, Done)
        assert len(done.state.selected_drafts()) == 1
