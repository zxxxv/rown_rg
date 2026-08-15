"""색인 끝 검색 리허설(_rehearse)과 조건부 게이트(_rehearsal_gate) 검증.

DB 의존(현재 index_version·결과 영속·flows 조회)은 전부 monkeypatch로 끊는다 —
여기서 검증하는 것은 판정과 흐름이다: 밴드 분류, HyDE 재검색 1회, 구성형 절 구분,
재개방 예산(2회), 게이트 payload 구성.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.state import ProjectState
from src.core.types import ProjectStage, RetrievedChunk, ReviewGate, SectionPlan
from src.workflows import stages
from src.workflows.pipeline import _rehearsal_gate


def _chunks(n: int) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id=uuid4(), source_id=uuid4(), content="근거", score=0.9)
        for _ in range(n)
    ]


def _state(plan_counts: dict[str, int], options: dict | None = None) -> ProjectState:
    """plan_counts: 절 제목 → (fake 검색이 돌려줄 청크 수)."""
    plan = [
        SectionPlan(chapter_number=1, section_number=i, title=title)
        for i, title in enumerate(plan_counts, start=1)
    ]
    return ProjectState(
        user_id=uuid4(),
        topic="주제",
        section_plan=plan,
        current_stage=ProjectStage.RESEARCHING,
        options=options or {},
    )


@pytest.fixture
def rehearse_env(monkeypatch: pytest.MonkeyPatch):
    """리허설의 DB·검색 의존을 fake로 — 절 제목별 청크 수와 기록 수집기를 돌려준다."""
    env = {
        "counts": {},  # title → 기본 검색 청크 수
        "hyde_counts": {},  # title → HyDE 재검색 청크 수 (없으면 기본과 동일)
        "stored": [],
        "flows": {},
        "probe_hits": 1,  # 요약 트리 대조 결과 수 (0이면 raptor_gap)
        "hyde_calls": [],
    }

    async def fake_retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        return _chunks(env["counts"].get(section.title, 0))

    async def fake_hyde_retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        env["hyde_calls"].append(section.title)
        return _chunks(env["hyde_counts"].get(section.title, env["counts"].get(section.title, 0)))

    async def fake_version(_pid) -> int:
        return 5

    async def fake_store(pid, sid, **kwargs) -> None:
        env["stored"].append(kwargs)

    async def fake_flows(_pid) -> dict[str, int]:
        return env["flows"]

    async def fake_catalog(_uid) -> dict:
        return {}

    async def fake_sources(_pid) -> list:
        return []

    def fake_probe(_state):
        async def probe(_query: str):
            return _chunks(env["probe_hits"])

        return probe

    monkeypatch.setattr(stages, "_retriever_factory", lambda state: fake_retrieve)
    monkeypatch.setattr(stages, "_hyde_forced_retriever", lambda state: fake_hyde_retrieve)
    monkeypatch.setattr(stages, "_analyst_catalog", fake_catalog)
    monkeypatch.setattr(stages, "_design_flows_inbound", fake_flows)
    monkeypatch.setattr(stages, "_summary_probe", fake_probe)
    monkeypatch.setattr(stages, "_adopted_source_refs", fake_sources)
    monkeypatch.setattr("src.services.retrieval.rehearsal.current_index_version", fake_version)
    monkeypatch.setattr("src.services.retrieval.rehearsal.store_rehearsal", fake_store)
    return env


class TestRehearseBands:
    async def test_rich_sections_pass_without_reopen(self, rehearse_env):
        rehearse_env["counts"] = {"개요": 12, "분석": 15}
        out = await stages._rehearse(_state({"개요": 12, "분석": 15}))
        report = out.rehearsal
        assert report is not None
        assert [s["band"] for s in report["sections"]] == ["ok", "ok"]
        assert report["reopen"] is False
        # 결과는 절마다 현재 버전으로 영속된다 — 작성이 그대로 재사용.
        assert len(rehearse_env["stored"]) == 2
        assert all(s["index_version"] == 5 for s in rehearse_env["stored"])

    async def test_hyde_band_retries_once_and_keeps_better(self, rehearse_env):
        rehearse_env["counts"] = {"부족": 5}
        rehearse_env["hyde_counts"] = {"부족": 13}
        out = await stages._rehearse(_state({"부족": 5}))
        sec = out.rehearsal["sections"][0]
        assert rehearse_env["hyde_calls"] == ["부족"]  # 재검색은 1회
        assert sec["hyde_used"] is True
        assert sec["band"] == "ok"  # 보강으로 승격
        assert sec["floor_passed"] == 13
        assert out.rehearsal["reopen"] is False

    async def test_hyde_worse_result_is_discarded(self, rehearse_env):
        rehearse_env["counts"] = {"부족": 6}
        rehearse_env["hyde_counts"] = {"부족": 2}
        out = await stages._rehearse(_state({"부족": 6}))
        sec = out.rehearsal["sections"][0]
        assert sec["hyde_used"] is False
        assert sec["floor_passed"] == 6  # 나빠진 재검색은 버린다
        assert sec["band"] == "hyde"  # 부족한 채 진행(작성이 분량을 깎는다)

    async def test_empty_section_reopens_gate_and_bumps_budget(self, rehearse_env):
        rehearse_env["counts"] = {"공백": 0, "정상": 12}
        rehearse_env["probe_hits"] = 0
        out = await stages._rehearse(_state({"공백": 0, "정상": 12}))
        report = out.rehearsal
        empty = next(s for s in report["sections"] if s["label"].endswith("공백"))
        assert empty["band"] == "empty"
        assert empty["raptor_gap"] is True  # 클러스터에도 없음 = 자료 자체가 없다
        assert report["reopen"] is True
        assert out.options["_rehearsal_reopens"] == 1

    async def test_constructive_section_warns_but_does_not_reopen(self, rehearse_env):
        # 구성형 절(flows 수신 2+)은 자료를 더 모아도 검색으로 안 채워진다 —
        # 재개방 트리거에서 빼고 경고로만 구분한다.
        rehearse_env["counts"] = {"종합": 0}
        rehearse_env["flows"] = {"1.1": 2}
        out = await stages._rehearse(_state({"종합": 0}))
        sec = out.rehearsal["sections"][0]
        assert sec["band"] == "empty"
        assert sec["constructive"] is True
        assert out.rehearsal["reopen"] is False
        assert "_rehearsal_reopens" not in (out.options or {})

    async def test_reopen_budget_exhausted_escalates_and_proceeds(self, rehearse_env):
        rehearse_env["counts"] = {"공백": 0}
        out = await stages._rehearse(_state({"공백": 0}, options={"_rehearsal_reopens": 2}))
        assert out.rehearsal["reopen"] is False
        assert out.rehearsal["escalated"] is True


class TestRehearsalGate:
    def test_no_reopen_returns_none_and_pipeline_continues(self):
        state = _state({"개요": 1}).model_copy(update={"rehearsal": {"reopen": False}})
        assert _rehearsal_gate(state) is None

    def test_reopen_builds_source_pool_gate_with_gap_payload(self):
        report = {
            "reopen": True,
            "reopens_used": 1,
            "sections": [
                {
                    "label": "1.1 공백",
                    "band": "empty",
                    "floor_passed": 1,
                    "needed": 12,
                    "constructive": False,
                    "raptor_gap": True,
                },
                {"label": "1.2 정상", "band": "ok", "floor_passed": 12, "needed": 12},
            ],
        }
        state = _state({"공백": 0, "정상": 12}).model_copy(update={"rehearsal": report})
        review = _rehearsal_gate(state)
        assert review is not None
        assert review.gate == ReviewGate.SOURCE_POOL
        gaps = review.payload["rehearsal"]["empty_sections"]
        assert [g["label"] for g in gaps] == ["1.1 공백"]
        assert gaps[0]["raptor_gap"] is True
        assert review.payload["rehearsal"]["reopens_used"] == 1
