"""단계는 산출물에서 되짚는다 — status가 두 가지를 뜻하던 병의 회귀 방지.

컬럼 하나가 "어디까지 왔나"(진척)와 "지금 뭐가 도나"(실행)를 겸했다. 러너가 멈춘 뒤에도
마지막 실행 단계가 남아, 본문이 다 쓰인 보고서가 "자료 검색 중"으로 보였다(운영 DB
실측 2026-08-26: 8건 중 3건이 산출물과 어긋났다).
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.core.types import ProjectStage
from src.db.models.project import Project
from src.services.projects.derive import ProjectFacts, derive_stage


def _facts(**kw) -> ProjectFacts:
    base = {
        "has_brief": False,
        "n_sources": 0,
        "n_chunks": 0,
        "n_sections": 0,
        "n_written": 0,
        "finalized": False,
    }
    return ProjectFacts(**{**base, "finalized": kw.pop("finalized", False), **kw})


def _project(status: str, *, completed_at: datetime | None = None) -> Project:
    return Project(title="t", topic="t", config={}, status=status, completed_at=completed_at)


class TestDeriveStage:
    def test_idle_project_with_a_body_is_reviewing_not_researching(self):
        """병의 본체 — 러너가 남긴 'researching'을 산출물이 덮는다."""
        p = _project(ProjectStage.RESEARCHING.value)
        f = _facts(n_sources=31, n_chunks=1484, n_sections=7, n_written=7)
        assert derive_stage(p, f, running=False) is ProjectStage.REVIEWING

    def test_running_trusts_the_runner(self):
        """도는 동안은 러너가 유일한 증인이다 — 산출물은 아직 안 만들어졌을 뿐이다."""
        p = _project(ProjectStage.RESEARCHING.value)
        f = _facts(n_sources=31, n_chunks=1484, n_sections=7, n_written=7)
        assert derive_stage(p, f, running=True) is ProjectStage.RESEARCHING

    def test_ladder_from_artifacts(self):
        p = _project(ProjectStage.WRITING.value)
        assert derive_stage(p, _facts(), running=False) is ProjectStage.CREATED
        assert derive_stage(p, _facts(has_brief=True), running=False) is ProjectStage.PLANNING
        assert (
            derive_stage(p, _facts(has_brief=True, n_sources=3), running=False)
            is ProjectStage.RESEARCHING
        )
        assert (
            derive_stage(p, _facts(has_brief=True, n_sources=3, n_chunks=90), running=False)
            is ProjectStage.WRITING
        )

    def test_empty_sections_do_not_count_as_written(self):
        """목차만 심어 둔 빈 절은 본문이 아니다 - 세면 '검토 차례'로 건너뛴다."""
        p = _project(ProjectStage.WRITING.value)
        f = _facts(n_sources=3, n_chunks=90, n_sections=20, n_written=0)
        assert derive_stage(p, f, running=False) is ProjectStage.WRITING

    def test_finalized_is_completed_even_while_idle(self):
        p = _project(ProjectStage.REVIEWING.value, completed_at=datetime.now(UTC))
        f = _facts(n_sources=3, n_chunks=90, n_sections=20, n_written=20, finalized=True)
        assert derive_stage(p, f, running=False) is ProjectStage.COMPLETED

    def test_reopened_falls_back_to_review_on_its_own(self):
        """확정을 풀면(completed_at=None) 파생값이 저절로 내려온다 - 따로 정할 게 없다."""
        p = _project(ProjectStage.COMPLETED.value)
        f = _facts(n_sources=3, n_chunks=90, n_sections=20, n_written=20, finalized=False)
        assert derive_stage(p, f, running=False) is ProjectStage.REVIEWING

    def test_human_made_facts_are_never_overridden(self):
        """보관·취소는 산출물로 되짚을 수 없다 - 사람이 만든 사실이라 컬럼이 정본이다."""
        done = _facts(n_sources=3, n_chunks=90, n_sections=20, n_written=20)
        for stage in (ProjectStage.ARCHIVED, ProjectStage.CANCELLED):
            p = _project(stage.value)
            assert derive_stage(p, done, running=False) is stage
            assert derive_stage(p, done, running=True) is stage
