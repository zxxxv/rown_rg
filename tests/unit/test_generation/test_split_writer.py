"""분할 생성(split_writer) 단위 검증 — 실LLM·실DB 없이 주입/몽키패치로.

프로토타입(exp_split2, 2026-08-07)에서 실증된 계약을 고정한다:
- 파트 수 = clamp(ceil(min_chars/4500), 1, 6), 분할 off·목표 없음 → 1
- 근거 배타 배정: 중복 없음·캡·빈약 파트 병합, 병합 시 생존 인덱스로 제목 정렬
- 파트 tail: 허용 인용 목록·경계 지시·부정 목록(소제목/수치)만 — 앞 파트 본문 전사 없음
- 결합 draft: [n]이 전체 풀 번호 그대로 매핑, 파트 요청은 cache_prefix_messages=1
- 계획 실패·풀 빈약 시 None(단일 호출 폴백 신호)
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.types import RetrievedChunk, SectionPlan
from src.services.generation import split_writer
from src.services.generation.split_writer import (
    _numbers_used,
    _part_tail,
    assign_by_similarity,
    generate_section_split,
    plan_part_count,
)
from src.services.generation.writer_context import WriterContext


def _chunk(content: str) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=uuid4(), source_id=uuid4(), content=content, score=1.0)


def _plan() -> SectionPlan:
    return SectionPlan(
        section_id=uuid4(), chapter_number=3, section_number=2, title="밸류체인 및 생태계 분석"
    )


def _ctx() -> WriterContext:
    return WriterContext(
        system="시스템", guidance="", max_tokens=24000, min_chars=12000, max_chars=45000
    )


class FakeLLM:
    """계획 1콜 + 파트 N콜을 순서대로 돌려주고 요청을 기록한다."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        content = self._responses[len(self.requests) - 1]
        return CompletionResponse(
            content=content,
            input_tokens=10,
            output_tokens=10,
            model=request.model,
            stop_reason="end_turn",
        )


class _FakeLLMWithStops(FakeLLM):
    """콜 순서별 stop_reason을 지정 — 파트 절단(미완결) 시나리오용."""

    def __init__(self, responses: list[str], stops: list[str]) -> None:
        super().__init__(responses)
        self._stops = stops

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        response = await super().complete(request)
        return response.model_copy(update={"stop_reason": self._stops[len(self.requests) - 1]})


class FakeEmbedder:
    """제목 텍스트에 심은 축 마커(axis0/axis1)로 결정적 단위 벡터를 돌려준다."""

    async def embed(self, text: str) -> Any:
        from types import SimpleNamespace

        vec = [1.0, 0.0] if "axis0" in text else [0.0, 1.0]
        return SimpleNamespace(embedding=vec)


class TestPlanPartCount:
    def test_no_target_is_single(self):
        assert plan_part_count(None) == 1
        assert plan_part_count(0) == 1

    def test_scales_with_min_chars(self):
        # 파트당 2,250자 기준(2026-08-08 파트 배수 상향)
        assert plan_part_count(2250) == 1
        assert plan_part_count(12000) == 6
        assert plan_part_count(20000) == 9

    def test_capped_at_max_parts(self):
        assert plan_part_count(45000) == 10
        assert plan_part_count(999999) == 10

    def test_disabled_switch(self, monkeypatch):
        from src.core.config import settings

        monkeypatch.setattr(settings, "write_split_enabled", False)
        assert plan_part_count(45000) == 1


class TestAssignBySimilarity:
    def test_exclusive_and_exhaustive(self):
        # 축 정렬된 6청크 × 2파트 — 배타·전량 배정
        chunks = [[1, 0], [1, 0], [1, 0], [0, 1], [0, 1], [0, 1]]
        parts = [[1, 0], [0, 1]]
        groups, surviving = assign_by_similarity(chunks, parts)
        assert surviving == [0, 1]
        flat = sorted(n for g in groups for n in g)
        assert flat == [1, 2, 3, 4, 5, 6]  # 중복 없이 전량
        assert groups[0] == [1, 2, 3]
        assert groups[1] == [4, 5, 6]

    def test_merges_starved_part(self):
        # 파트2에 쏠릴 청크가 없음 → 파트2 제거 후 재배정, 생존 인덱스 보고
        chunks = [[1, 0]] * 6
        parts = [[1, 0], [0, 1]]
        groups, surviving = assign_by_similarity(chunks, parts)
        assert surviving == [1] or surviving == [0]
        assert len(groups) == 1
        assert sorted(groups[0]) == [1, 2, 3, 4, 5, 6]

    def test_deterministic(self):
        chunks = [[0.9, 0.1], [0.8, 0.2], [0.2, 0.8], [0.1, 0.9], [0.6, 0.4], [0.4, 0.6]]
        parts = [[1, 0], [0, 1]]
        a = assign_by_similarity(chunks, parts)
        b = assign_by_similarity(chunks, parts)
        assert a == b


class TestPartTail:
    def test_contains_allowed_and_negatives_only(self):
        tail = _part_tail(
            2,
            3,
            "국내 기업",
            ["밸류체인", "국내 기업", "규제"],
            [3, 7],
            ["이미 다룬 소제목"],
            ["45.52", "9.21%"],
        )
        assert "3, 7번" in tail  # 허용 근거 목록 - 출처 표기는 (출처 n)이라 대괄호를 안 쓴다
        assert "파트 2/3" in tail
        assert "이미 다룬 소제목" in tail
        assert "45.52" in tail
        assert "밸류체인" in tail and "규제" in tail  # 다른 파트 침범 금지 목록
        assert "□" in tail  # 개조식 형식 유지 지시

    def test_last_part_allows_closing(self):
        first = _part_tail(1, 3, "a", ["a", "b", "c"], [1], [], [])
        last = _part_tail(3, 3, "c", ["a", "b", "c"], [2], [], [])
        assert "마무리를" not in first
        assert "종합·마무리·결론 문단을 쓰지 마라" in first
        assert "마지막 파트" in last

    def test_numbers_used_filters_years_and_single_digits(self):
        nums = _numbers_used(["2024년 45.52억 달러, 5개, 9.21% 성장, 2025년"])
        assert "45.52" in nums and "9.21%" in nums
        assert "2024" not in nums and "5" not in nums


@pytest.mark.asyncio
class TestGenerateSectionSplit:
    async def _run(self, monkeypatch, fake: FakeLLM, n_chunks: int = 6):
        chunks = [
            _chunk(f"axis0 근거 {i}" if i < n_chunks // 2 else f"axis1 근거 {i}")
            for i in range(n_chunks)
        ]

        async def fake_vectors(citable):
            return [[1.0, 0.0] if "axis0" in c.content else [0.0, 1.0] for c in citable]

        monkeypatch.setattr(split_writer, "_load_chunk_vectors", fake_vectors)
        draft = await generate_section_split(
            _plan(),
            chunks,
            n_parts=2,
            context=_ctx(),
            model="claude-haiku-4-5",
            client=fake,
            embedder=FakeEmbedder(),
        )
        return draft, chunks

    async def test_combines_parts_and_maps_citations(self, monkeypatch):
        fake = FakeLLM(
            [
                '["axis0 소주제", "axis1 소주제"]',  # 계획
                "□ 파트1 본문 [1][2]",
                "□ 파트2 본문 [4]",
            ]
        )
        draft, chunks = await self._run(monkeypatch, fake)
        assert draft is not None
        assert "파트1 본문" in draft.content and "파트2 본문" in draft.content
        # [1],[2],[4] → 전체 풀 번호 그대로 chunk_id 매핑
        assert draft.cited_chunk_ids == [
            chunks[0].chunk_id,
            chunks[1].chunk_id,
            chunks[3].chunk_id,
        ]

    async def test_part_requests_share_cached_prefix(self, monkeypatch):
        fake = FakeLLM(['["axis0 소주제", "axis1 소주제"]', "□ p1 [1]", "□ p2 [4]"])
        await self._run(monkeypatch, fake)
        part_reqs = fake.requests[1:]
        assert len(part_reqs) == 2
        prefixes = {r.messages[0].content for r in part_reqs}
        assert len(prefixes) == 1  # 공유 프리픽스 — 캐시 적중의 전제
        assert all(r.cache_prefix_messages == 1 for r in part_reqs)
        assert all(len(r.messages) == 2 for r in part_reqs)
        # 파트2 tail에 앞 파트 '본문'이 아니라 소제목만 — 전사 오염 차단 계약
        assert "p1" not in part_reqs[1].messages[1].content or "□ p1" not in (
            part_reqs[1].messages[1].content
        )

    async def test_plan_failure_falls_back(self, monkeypatch):
        fake = FakeLLM(["JSON 아님"])
        draft, _ = await self._run(monkeypatch, fake)
        assert draft is None

    async def test_plan_model_routes_only_plan_call(self, monkeypatch):
        """구조 품질용 손잡이 — 계획 1콜만 상위 모델, 본문 파트는 저가 모델 유지."""
        fake = FakeLLM(['["axis0 소주제", "axis1 소주제"]', "□ p1 [1]", "□ p2 [4]"])
        chunks = [_chunk(f"axis0 근거 {i}" if i < 3 else f"axis1 근거 {i}") for i in range(6)]

        async def fake_vectors(citable):
            return [[1.0, 0.0] if "axis0" in c.content else [0.0, 1.0] for c in citable]

        monkeypatch.setattr(split_writer, "_load_chunk_vectors", fake_vectors)
        await generate_section_split(
            _plan(),
            chunks,
            n_parts=2,
            context=_ctx(),
            model="claude-haiku-4-5",
            plan_model="claude-sonnet-4-6",
            client=fake,
            embedder=FakeEmbedder(),
        )
        assert fake.requests[0].model == "claude-sonnet-4-6"  # 계획
        assert [r.model for r in fake.requests[1:]] == ["claude-haiku-4-5"] * 2  # 본문

    async def test_small_pool_falls_back(self, monkeypatch):
        fake = FakeLLM(['["a", "b"]'])
        draft, _ = await self._run(monkeypatch, fake, n_chunks=4)  # < 3*2
        assert draft is None
        assert fake.requests == []  # 계획 콜도 안 나감

    async def test_truncated_part_retried_once(self, monkeypatch):
        """파트 미완결(max_tokens 컷)은 같은 호출 1회 재시도 — 성공하면 결합 계속."""
        fake = _FakeLLMWithStops(
            [
                '["axis0 소주제", "axis1 소주제"]',
                "□ 끊긴 파트1 (출처 ",  # max_tokens 컷
                "□ 완결된 파트1 [1]",  # 재시도 성공
                "□ 파트2 [4]",
            ],
            ["end_turn", "max_tokens", "end_turn", "end_turn"],
        )
        draft, _ = await self._run(monkeypatch, fake)
        assert draft is not None
        assert "완결된 파트1" in draft.content
        assert "끊긴 파트1" not in draft.content
        assert len(fake.requests) == 4  # 계획 + 파트1 + 파트1 재시도 + 파트2

    async def test_truncated_part_twice_falls_back(self, monkeypatch):
        """재시도도 미완결이면 분할 포기(None) — 토막을 결합본에 심지 않는다."""
        fake = _FakeLLMWithStops(
            ['["axis0 소주제", "axis1 소주제"]', "□ 끊긴 (출처 ", "□ 또 끊긴 (출처 "],
            ["end_turn", "max_tokens", "max_tokens"],
        )
        draft, _ = await self._run(monkeypatch, fake)
        assert draft is None
        assert len(fake.requests) == 3  # 계획 + 파트1 + 파트1 재시도에서 중단

    async def test_merged_single_part_falls_back(self, monkeypatch):
        # 두 소주제가 모두 axis0 → 한 파트로 병합 → None 폴백
        fake = FakeLLM(['["axis0 소주제 하나", "axis0 소주제 둘"]'])
        chunks = [_chunk(f"axis0 근거 {i}") for i in range(6)]

        async def fake_vectors(citable):
            return [[1.0, 0.0] for _ in citable]

        monkeypatch.setattr(split_writer, "_load_chunk_vectors", fake_vectors)
        draft = await generate_section_split(
            _plan(),
            chunks,
            n_parts=2,
            context=_ctx(),
            model="claude-haiku-4-5",
            client=fake,
            embedder=FakeEmbedder(),
        )
        assert draft is None
