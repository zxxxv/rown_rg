"""취소한 런의 재개 지점 — cancelled는 종료가 아니라 '멈춤'이다.

화면은 취소된 프로젝트에 "다시 시작"을 띄우고 주석도 재개 가능이라 적혀 있었지만
가드가 cancelled를 종료 상태로 묶어 422가 났다(2026-08-10). 재개가 가능해진 뒤에도
직전 단계를 모르면 처음부터 다시 돌아야 하므로 config.cancelled_from을 남긴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.core.types import ProjectStage
from src.workflows.runner import _state_from_project


class _Row:
    """Project 행 최소 스텁 — _state_from_project가 읽는 필드만."""

    def __init__(self, status: str, config: dict | None = None):
        self.id = uuid.uuid4()
        self.owner_id = uuid.uuid4()
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)
        self.topic = "주제"
        self.title = "제목"
        self.preset = None
        self.depth_mode = "full_report"
        self.status = status
        self.config = config or {}


class TestCancelledResumePoint:
    def test_resumes_at_stage_recorded_on_cancel(self):
        row = _Row(ProjectStage.CANCELLED.value, {"cancelled_from": ProjectStage.RESEARCHING.value})
        assert _state_from_project(row).current_stage is ProjectStage.RESEARCHING

    def test_writing_normalized_to_indexing(self):
        # writing은 척추 단계가 아니라 INDEXING 구간의 실행 표시다(완료 둔갑 방지).
        row = _Row(ProjectStage.CANCELLED.value, {"cancelled_from": ProjectStage.WRITING.value})
        assert _state_from_project(row).current_stage is ProjectStage.INDEXING

    def test_without_record_starts_from_created(self):
        assert _state_from_project(_Row(ProjectStage.CANCELLED.value)).current_stage is (
            ProjectStage.CREATED
        )

    def test_garbage_record_falls_back_to_created(self):
        row = _Row(ProjectStage.CANCELLED.value, {"cancelled_from": "존재하지않는단계"})
        assert _state_from_project(row).current_stage is ProjectStage.CREATED
