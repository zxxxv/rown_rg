"""후보 생성기 검증 — stub LLMClient로 네트워크 없이 파싱·다양성 확인."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.types import RetrievedChunk, SectionPlan
from src.services.generation.candidates import (
    _build_prompt,
    _extract_cited_ids,
    generate_section_candidates,
)


class _StubClient:
    """호출 순서대로 미리 준비한 텍스트를 돌려주는 가짜 LLMClient."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        text = self._responses[len(self.calls) % len(self._responses)]
        self.calls.append(request)
        return CompletionResponse(
            content=text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


def _chunks(n: int) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id=uuid4(), source_id=uuid4(), content=f"근거{i}", score=0.9)
        for i in range(n)
    ]


def _section() -> SectionPlan:
    return SectionPlan(chapter_number=1, section_number=2, title="분석")


# ---------- _extract_cited_ids ----------


class TestExtractCitedIds:
    def test_maps_markers_to_chunk_ids(self):
        chunks = _chunks(3)
        ids = _extract_cited_ids("주장 A [1]. 주장 B [3].", chunks)
        assert ids == [chunks[0].chunk_id, chunks[2].chunk_id]

    def test_out_of_range_marker_ignored(self):
        chunks = _chunks(2)
        # [99]는 범위 밖 → 버림. 유효한 [1]만 남음.
        ids = _extract_cited_ids("본문 [1] 그리고 [99]", chunks)
        assert ids == [chunks[0].chunk_id]

    def test_duplicate_markers_deduped_in_order(self):
        chunks = _chunks(2)
        ids = _extract_cited_ids("[2] ... [1] ... [2] 다시", chunks)
        assert ids == [chunks[1].chunk_id, chunks[0].chunk_id]

    def test_no_markers_returns_empty(self):
        assert _extract_cited_ids("인용 없는 본문", _chunks(2)) == []


# ---------- _build_prompt ----------


class TestBuildPrompt:
    def test_numbers_chunks_from_one(self):
        prompt = _build_prompt(_section(), _chunks(2))
        assert "[1] 근거0" in prompt
        assert "[2] 근거1" in prompt
        assert "1.2 분석" in prompt

    def test_no_raw_uuid_in_prompt(self):
        chunks = _chunks(2)
        prompt = _build_prompt(_section(), chunks)
        # UUID는 프롬프트에 절대 노출되지 않아야 (모델이 복제 못 하므로).
        assert str(chunks[0].chunk_id) not in prompt


# ---------- RAPTOR 요약 분리 (배경 맥락 — 인용 불가) ----------


def _summary(content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(), source_id=uuid4(), content=content, score=0.5, is_summary=True
    )


class TestSummaryContextSplit:
    def test_summaries_unnumbered_in_context_block(self):
        chunks = [_summary("전체 요약입니다"), *_chunks(2)]
        prompt = _build_prompt(_section(), chunks)
        assert "배경 맥락" in prompt
        assert "- 전체 요약입니다" in prompt
        # 번호 매김은 leaf부터 1번 — 요약이 번호를 차지하지 않는다.
        assert "[1] 근거0" in prompt
        assert "[2] 근거1" in prompt

    def test_no_context_block_without_summaries(self):
        assert "배경 맥락" not in _build_prompt(_section(), _chunks(1))

    async def test_cited_ids_map_to_leaf_pool_only(self):
        summary = _summary("요약")
        leafs = _chunks(1)
        stub = _StubClient(["본문 [1]"])
        drafts = await generate_section_candidates(_section(), [summary, *leafs], n=1, client=stub)
        # [1]은 인용 가능 풀(leaf)의 첫 항목 — 요약 노드가 아니다.
        assert drafts[0].cited_chunk_ids == [leafs[0].chunk_id]


# ---------- generate_section_candidates ----------


class TestGenerateSectionCandidates:
    async def test_generates_n_drafts_with_parsed_citations(self):
        chunks = _chunks(2)
        section = _section()
        stub = _StubClient(["초안 하나 [1].", "초안 둘 [2]."])
        drafts = await generate_section_candidates(section, chunks, n=2, client=stub)
        assert len(drafts) == 2
        assert all(d.section_id == section.section_id for d in drafts)
        assert drafts[0].cited_chunk_ids == [chunks[0].chunk_id]
        assert drafts[1].cited_chunk_ids == [chunks[1].chunk_id]

    async def test_temperature_varies_across_candidates(self):
        chunks = _chunks(1)
        stub = _StubClient(["본문 [1]"])
        await generate_section_candidates(
            _section(), chunks, n=3, client=stub, base_temperature=0.7
        )
        temps = [req.temperature for req in stub.calls]
        assert temps == [0.7, pytest.approx(0.85), pytest.approx(1.0)]

    async def test_temperature_capped_at_ceiling(self):
        chunks = _chunks(1)
        stub = _StubClient(["본문 [1]"])
        await generate_section_candidates(
            _section(), chunks, n=5, client=stub, base_temperature=0.8
        )
        temps = [req.temperature for req in stub.calls]
        assert max(temps) <= 1.0
        assert temps[-1] == pytest.approx(1.0)

    async def test_system_prompt_and_model_forwarded(self):
        stub = _StubClient(["본문 [1]"])
        await generate_section_candidates(
            _section(), _chunks(1), n=1, client=stub, model="gemini-2.5-flash"
        )
        assert stub.calls[0].model == "gemini-2.5-flash"
        assert stub.calls[0].system is not None
        assert "근거" in stub.calls[0].system

    async def test_n_less_than_one_raises(self):
        with pytest.raises(ValueError, match="n은 1 이상"):
            await generate_section_candidates(
                _section(), _chunks(1), n=0, client=_StubClient(["x"])
            )
