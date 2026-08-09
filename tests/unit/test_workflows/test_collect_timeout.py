"""수집 챕터 타임아웃 — 응답이 안 오는 콜이 실행을 통째로 붙잡지 않는다.

실측(2026-08-10): 자료 17건을 모은 뒤 research.collect 안에서 28분 무활동. 화면은
"자료 수집 중"으로 계속 돌아 겉으로는 정상처럼 보였다. 작성 경로는 스트리밍으로
10분 상한을 우회했지만 수집 경로에는 상한이 없었다.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.core.state import ProjectState
from src.core.types import SectionPlan
from src.services.research import ResearchResult, ResearchSpec
from src.workflows import stages

pytestmark = pytest.mark.asyncio


def _state() -> ProjectState:
    return ProjectState(
        project_id=uuid4(),
        user_id=uuid4(),
        topic="주제",
        section_plan=[
            SectionPlan(section_id=uuid4(), chapter_number=1, section_number=1, title="1장 절"),
            SectionPlan(section_id=uuid4(), chapter_number=2, section_number=1, title="2장 절"),
        ],
    )


def _empty_result(spec: ResearchSpec) -> ResearchResult:
    return ResearchResult(spec=spec, sources=[], manifest={"sources": []}, coverage_gaps=[])


class TestChapterTimeout:
    async def test_hung_chapter_is_dropped_and_next_one_runs(self, monkeypatch):
        """멈춘 챕터는 버리고 다음 챕터를 계속 돈다 — 전체가 같이 멈추지 않는다."""
        monkeypatch.setattr(stages.settings, "research_chapter_timeout_seconds", 0.05)
        seen: list[int] = []

        async def _collect(spec, *, model, project_id, chapter):
            seen.append(chapter)
            if chapter == 1:
                await asyncio.sleep(5)  # 응답 없는 콜
            return _empty_result(spec)

        monkeypatch.setattr(stages, "_collect_chapter", _collect)
        monkeypatch.setattr(stages, "_web_indexer_factory", lambda: object())

        refs = await stages._collect_sources(_state(), exclude_keys=set())

        assert seen == [1, 2]  # 1장에서 막히지 않고 2장까지 갔다
        assert refs == []

    async def test_all_chapters_hung_raises(self, monkeypatch):
        """전 챕터가 멈추면 시스템 문제다 — 빈 게이트를 여는 대신 실행 실패로 올린다."""
        monkeypatch.setattr(stages.settings, "research_chapter_timeout_seconds", 0.05)

        async def _hang(spec, *, model, project_id, chapter):
            await asyncio.sleep(5)
            return _empty_result(spec)

        monkeypatch.setattr(stages, "_collect_chapter", _hang)
        monkeypatch.setattr(stages, "_web_indexer_factory", lambda: object())

        with pytest.raises(Exception, match="수집이"):
            await stages._collect_sources(_state(), exclude_keys=set())
