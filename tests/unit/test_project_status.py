from __future__ import annotations

import re

from sqlalchemy import CheckConstraint

from src.api.schemas.project import ProjectStatus
from src.core.types import ProjectStage
from src.db.models.project import Project

# 프로젝트 라이프사이클의 정본(canonical) 값 집합.
# 이 집합을 바꾸면 DB CheckConstraint 마이그레이션도 함께 갱신해야 한다 (의도적 변경 강제).
# planning 추가(2026-08-14, 마이그레이션 0038): 수집 전 설계 브리프 게이트가 멈추는 자리.
# 게이트보다 stage가 먼저 전이해야 재개 시 같은 게이트가 다시 열리지 않는다(pipeline.PHASES).
EXPECTED_STATUS_VALUES = {
    "created",
    "planning",
    "researching",
    "indexing",
    "writing",
    "reviewing",
    "completed",
    "archived",
    "cancelled",
}


def test_project_stage_is_canonical_set() -> None:
    """ProjectStage(단일 진실)가 정본 값 집합과 정확히 일치해야 한다."""
    assert {stage.value for stage in ProjectStage} == EXPECTED_STATUS_VALUES


def test_api_status_derives_from_enum() -> None:
    """API ProjectStatus는 별도 정의 없이 ProjectStage에서 파생되어야 한다."""
    assert ProjectStatus is ProjectStage


def test_db_check_constraint_matches_enum() -> None:
    """projects.status CheckConstraint 허용 값이 ProjectStage와 어긋나지 않아야 한다."""
    constraint = next(
        c
        for c in Project.__table__.constraints
        if isinstance(c, CheckConstraint) and str(c.sqltext).startswith("status IN")
    )
    allowed = set(re.findall(r"'([^']*)'", str(constraint.sqltext)))
    assert allowed == {stage.value for stage in ProjectStage}
