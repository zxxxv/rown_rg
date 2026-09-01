"""검색 리허설 — 밴드 판정(분량 유도 경계)과 캐시 retriever(동근거 계약) 검증."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.types import RetrievedChunk, SectionPlan
from src.services.retrieval import rehearsal
from src.services.retrieval.rehearsal import (
    BAND_EMPTY,
    BAND_HYDE,
    BAND_OK,
    classify,
    empty_floor,
    make_cached_retriever,
    needed_evidence,
)


def _chunk(score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=uuid4(), source_id=uuid4(), content="근거 본문", score=score)


class TestBandBoundaries:
    def test_needed_derives_from_volume_target(self):
        # 필요 근거 = ceil(min_chars / 750) — scale_for_evidence와 같은 상수라
        # "리허설 ok = 작성에서 분량 안 깎임"이 성립한다.
        assert needed_evidence(9_000) == 12
        assert needed_evidence(3_000) == 4
        assert needed_evidence(100) == 1

    def test_no_volume_target_falls_back_to_start_value(self):
        # 시작값 합의(2026-08-15): ≥12 진행 / 4~11 HyDE / <4 공백.
        assert needed_evidence(None) == 12
        assert needed_evidence(0) == 12

    def test_empty_floor_is_third_of_needed_with_bottom(self):
        assert empty_floor(12) == 4
        assert empty_floor(4) == 3  # 바닥 3 — 근거 3개 미만이면 절을 쓸 수 없다
        assert empty_floor(30) == 10

    def test_start_value_bands_match_agreement(self):
        # min_chars 9,000(=needed 12) 기준으로 합의된 시작값 밴드가 그대로 나온다.
        needed = needed_evidence(9_000)
        assert classify(12, needed) == BAND_OK
        assert classify(11, needed) == BAND_HYDE
        assert classify(4, needed) == BAND_HYDE
        assert classify(3, needed) == BAND_EMPTY
        assert classify(0, needed) == BAND_EMPTY

    def test_small_section_scales_bands_down(self):
        # 목표가 작은 절은 경계도 작아진다 — 절대치 12를 강요하지 않는다.
        needed = needed_evidence(3_000)  # 4
        assert classify(4, needed) == BAND_OK
        assert classify(3, needed) == BAND_HYDE
        assert classify(2, needed) == BAND_EMPTY


class TestCachedRetriever:
    @pytest.fixture
    def section(self) -> SectionPlan:
        return SectionPlan(chapter_number=1, section_number=1, title="개요")

    async def test_cache_hit_skips_live_search(self, monkeypatch, section):
        cached = [_chunk(), _chunk()]
        calls: list[str] = []

        async def fake_load(pid, sid, version, plan_hash=""):
            calls.append(f"load:{version}")
            return cached

        async def inner(_section):
            raise AssertionError("캐시 적중 시 실검색이 호출되면 안 된다")

        # _with_source_titles는 실패 무해(try/except) — 제목 없이 그대로 돌아온다.
        monkeypatch.setattr(rehearsal, "load_rehearsal", fake_load)
        retrieve = make_cached_retriever(inner, uuid4(), index_version=7)
        result = await retrieve(section)
        assert [c.chunk_id for c in result] == [c.chunk_id for c in cached]
        assert calls == ["load:7"]

    async def test_cache_miss_falls_back_to_live_and_records(self, monkeypatch, section):
        live = [_chunk()]
        stored: list[dict] = []

        async def fake_load(pid, sid, version, plan_hash=""):
            return None

        async def fake_store(pid, sid, **kwargs):
            stored.append(kwargs)

        async def inner(_section):
            return live

        monkeypatch.setattr(rehearsal, "load_rehearsal", fake_load)
        monkeypatch.setattr(rehearsal, "store_rehearsal", fake_store)
        retrieve = make_cached_retriever(inner, uuid4(), index_version=3)
        result = await retrieve(section)
        assert result == live
        # 실검색 결과도 같은 버전으로 기록된다 — 같은 실행 내 재시도가 또 다른 근거를
        # 보지 않게(BAND_LIVE).
        assert stored and stored[0]["index_version"] == 3
        assert stored[0]["band"] == rehearsal.BAND_LIVE

    async def test_store_failure_does_not_break_write(self, monkeypatch, section):
        async def fake_load(pid, sid, version, plan_hash=""):
            return None

        async def broken_store(pid, sid, **kwargs):
            raise RuntimeError("db down")

        live = [_chunk()]

        async def inner(_section):
            return live

        monkeypatch.setattr(rehearsal, "load_rehearsal", fake_load)
        monkeypatch.setattr(rehearsal, "store_rehearsal", broken_store)
        retrieve = make_cached_retriever(inner, uuid4(), index_version=1)
        assert await retrieve(section) == live
