"""_commit_design_plan — 승인된 실행 계획의 config 커밋(사람 수정본 우선).

계획은 제안이고 확정은 사람 몫이다: 게이트에서 고친 계획(decision["ai_plan"])이
AI 원안(review payload)보다 우선해야 "본 대로 실행된다"가 성립한다.
"""

from __future__ import annotations

import uuid

from src.core.section_plan import config_with_plan
from src.core.types import SectionPlan
from src.db.models.project import Project
from src.workflows.runner import _commit_design_plan


class _FakeSession:
    """session.get(Project, id)만 흉내 — 커밋 로직은 재할당까지가 관찰 대상."""

    def __init__(self, project: Project) -> None:
        self._project = project

    async def get(self, _model: type, _pid: object) -> Project:
        return self._project


def _project_with_plan() -> tuple[Project, SectionPlan]:
    plan = SectionPlan(chapter_number=1, section_number=1, title="개요")
    project = Project(id=uuid.uuid4(), title="t", topic="주제", owner_id=uuid.uuid4())
    project.config = config_with_plan({}, [plan])
    return project, plan


def _payload(goal: str) -> dict:
    return {
        "ai_plan": {
            "sections": [
                {
                    "chapter": 1,
                    "section": 1,
                    "goal": goal,
                    "source_strategy": "",
                    "writing_plan": "",
                }
            ]
        }
    }


class TestCommitDesignPlan:
    async def test_원안이_커밋된다(self) -> None:
        project, plan = _project_with_plan()
        await _commit_design_plan(_FakeSession(project), project.id, _payload("AI 원안"), None)
        assert project.config["_design_plan"][str(plan.section_id)]["goal"] == "AI 원안"

    async def test_사람_수정본이_원안보다_우선한다(self) -> None:
        project, plan = _project_with_plan()
        decision = {"action": "approve", **_payload("사람 수정본")}
        await _commit_design_plan(_FakeSession(project), project.id, _payload("AI 원안"), decision)
        assert project.config["_design_plan"][str(plan.section_id)]["goal"] == "사람 수정본"

    async def test_모르는_절_좌표는_버려진다(self) -> None:
        """LLM이든 클라이언트든 없는 절을 보내면 그 항목만 무시(유령 주입 차단)."""
        project, _ = _project_with_plan()
        payload = {"ai_plan": {"sections": [{"chapter": 9, "section": 9, "goal": "유령"}]}}
        await _commit_design_plan(_FakeSession(project), project.id, payload, None)
        assert "_design_plan" not in project.config

    async def test_필드_상한이_잘린다(self) -> None:
        """편집이 열려 있으므로 폭주 입력이 프롬프트에 통째로 실리면 안 된다."""
        project, plan = _project_with_plan()
        await _commit_design_plan(_FakeSession(project), project.id, _payload("가" * 5000), None)
        assert len(project.config["_design_plan"][str(plan.section_id)]["goal"]) == 2000

    async def test_계획이_없으면_아무_것도_안_한다(self) -> None:
        project, _ = _project_with_plan()
        before = dict(project.config)
        await _commit_design_plan(_FakeSession(project), project.id, {}, {"action": "approve"})
        assert project.config == before
