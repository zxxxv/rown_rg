"""절 질의 분화 — 질의 집합 구성과 RRF 병합 계약.

절 하나가 던지는 1차 검색 질의가 '장 제목 + 절 제목' 하나뿐이라, 절이 무엇을 다루든
같은 자료가 왔다. 핵심 포인트와 담당 에이전트 관점을 각각 독립 질의로 올린다.
AnalystSpec.queries는 계약만 적혀 있고 소비처가 없던 죽은 필드였다(2026-08-19).
"""

from __future__ import annotations

import uuid

import pytest

from src.core.types import SectionPlan
from src.prompts import AnalystSpec
from src.services.retrieval.base import SearchHit
from src.services.retrieval.section import (
    MAX_SECTION_QUERIES,
    fuse_by_rank,
    section_query_set,
    section_search_query,
)


def _section(**kw) -> SectionPlan:
    base = {
        "chapter_number": 2,
        "section_number": 3,
        "chapter_title": "EU CBAM",
        "title": "적용 범위와 일정",
        "direction": "",
        "key_points": [],
        "analysts": [],
    }
    base.update(kw)
    return SectionPlan(**base)


def _spec(name: str, queries: list[str]) -> AnalystSpec:
    return AnalystSpec(
        id=f"x-{name}", name=name, cat="테스트", desc="", prompt="p", queries=queries
    )


def _hit(score: float) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        content="c",
        source_id=uuid.uuid4(),
        chunk_index=0,
        score=score,
        score_source="semantic",
    )


class TestQuerySet:
    def test_base_query_is_first(self):
        """기본 질의(장+절 제목)가 재현율 담당이라 항상 맨 앞이다."""
        qs = section_query_set(_section())
        assert qs[0] == section_search_query(_section())

    def test_key_points_become_separate_queries(self):
        """한 질의에 이어 붙이면 dense 벡터가 흐려진다 - 각각 독립 질의로 올린다."""
        qs = section_query_set(_section(key_points=["수입자 신고 의무", "전환기간 종료"]))
        assert any("수입자 신고 의무" in q for q in qs)
        assert any("전환기간 종료" in q for q in qs)
        # 절 맥락이 함께 실린다 - '전환기간 종료'만 던지면 주제를 벗어난다.
        for q in qs[1:]:
            assert "적용 범위와 일정" in q

    def test_analyst_queries_are_wired(self):
        """배정 에이전트의 질의 템플릿이 실제로 발화되는지 - 죽어 있던 계약."""
        catalog = {"SWOT분석": _spec("SWOT분석", ["{topic} SWOT", "{topic} 경쟁 분석"])}
        qs = section_query_set(_section(analysts=["SWOT분석"]), "글로벌 탄소규제 동향", catalog)
        assert any("SWOT" in q for q in qs)

    def test_analyst_query_is_anchored_on_the_section_not_the_topic(self):
        """주제로 치환하면 같은 에이전트를 쓴 절들이 또 같은 질의를 던진다 - 8/14 재발."""
        catalog = {"SWOT분석": _spec("SWOT분석", ["{topic} SWOT"])}
        a = section_query_set(
            _section(title="적용 범위", analysts=["SWOT분석"]), "같은주제", catalog
        )
        b = section_query_set(
            _section(title="산업별 영향", analysts=["SWOT분석"]), "같은주제", catalog
        )
        swot_a = [q for q in a if "SWOT" in q]
        swot_b = [q for q in b if "SWOT" in q]
        assert swot_a and swot_b
        assert swot_a != swot_b  # 절이 다르면 에이전트 질의도 달라야 한다
        assert "적용 범위" in swot_a[0]

    def test_falls_back_to_topic_when_section_title_empty(self):
        catalog = {"SWOT분석": _spec("SWOT분석", ["{topic} SWOT"])}
        qs = section_query_set(
            _section(title=" ", chapter_title="", analysts=["SWOT분석"]), "탄소규제", catalog
        )
        assert any("탄소규제" in q and "SWOT" in q for q in qs)

    def test_topic_placeholder_never_leaks(self):
        """치환할 말이 하나도 없어도 '{topic}'이 그대로 나가면 중괄호가 잡음 토큰이 된다."""
        catalog = {"SWOT분석": _spec("SWOT분석", ["{topic} SWOT"])}
        qs = section_query_set(
            _section(title=" ", chapter_title="", analysts=["SWOT분석"]), None, catalog
        )
        assert all("{topic}" not in q for q in qs)

    def test_unknown_analyst_is_skipped_not_fatal(self):
        qs = section_query_set(_section(analysts=["없는에이전트"]), "주제", {})
        assert qs == [section_search_query(_section())]

    def test_no_catalog_falls_back_to_old_behavior(self):
        """카탈로그를 못 읽는 호출부에서도 검색은 정상 동작해야 한다."""
        qs = section_query_set(_section(analysts=["SWOT분석"]), "주제", None)
        assert all("SWOT" not in q for q in qs)

    def test_queries_are_unique_and_capped(self):
        catalog = {
            f"a{i}": _spec(f"a{i}", [f"{{topic}} 관점{i}a", f"{{topic}} 관점{i}b"])
            for i in range(5)
        }
        qs = section_query_set(
            _section(key_points=["p1", "p2", "p3"], analysts=list(catalog)), "주제", catalog
        )
        assert len(qs) == len(set(qs))
        assert len(qs) <= MAX_SECTION_QUERIES

    def test_flag_off_returns_single_query(self, monkeypatch):
        """되돌리기·통제 실험이 설정 한 줄이어야 한다."""
        from src.core import config

        monkeypatch.setattr(config.settings, "retrieval_multi_query_enabled", False)
        catalog = {"SWOT분석": _spec("SWOT분석", ["{topic} SWOT"])}
        qs = section_query_set(_section(key_points=["p1"], analysts=["SWOT분석"]), "주제", catalog)
        assert qs == [section_search_query(_section())]


class TestFuseByRank:
    def test_common_hits_rise(self):
        """여러 질의가 공통으로 위에 올린 청크가 위로 온다 - RRF의 요점."""
        shared = _hit(0.5)
        a = [_hit(0.9), shared]
        b = [_hit(0.9), shared]
        fused = fuse_by_rank([a, b], limit=3)
        assert fused[0].chunk_id == shared.chunk_id

    def test_dedupes_by_chunk_id(self):
        shared = _hit(0.5)
        fused = fuse_by_rank([[shared], [shared], [shared]], limit=10)
        assert len(fused) == 1

    def test_keeps_best_original_score(self):
        """리랭커 off일 때 순서에 쓰이는 폴백 점수는 최댓값을 유지한다."""
        cid = uuid.uuid4()
        low, high = _hit(0.2), _hit(0.8)
        low.chunk_id = high.chunk_id = cid
        fused = fuse_by_rank([[low], [high]], limit=5)
        assert fused[0].score == pytest.approx(0.8)

    def test_respects_limit(self):
        fused = fuse_by_rank([[_hit(0.9) for _ in range(10)]], limit=4)
        assert len(fused) == 4

    def test_empty_input_is_empty(self):
        assert fuse_by_rank([], limit=5) == []
