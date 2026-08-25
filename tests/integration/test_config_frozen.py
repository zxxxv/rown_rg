"""완료된 보고서의 설정 동결 — 저장 버튼이 조용히 해를 끼치던 자리.

PATCH /config에 상태 가드가 없어서 완료된 프로젝트도 옵션을 덮어쓸 수 있었다.
독스트링은 "진행 중 프로젝트의 옵션"이라고 적혀 있었는데 검사를 안 했다.

목차를 바꾸면 실제로 해롭다:
- merge_config_update가 _section_plan·_design_plan을 버린다(목차 변경 = 어긋남 방지).
- 절 재작성이 config.outline을 **배열 위치로** 읽어 방향·핵심 포인트·에이전트를
  되살린다(_plan_for_row). 절을 더하거나 지우면 그 뒤 절들이 다른 절의 계획으로
  재작성된다 — 경고 없이.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.user import User

pytestmark = pytest.mark.asyncio

_OUTLINE = {
    "chapters": [{"title": "1장", "sections": [{"title": "1.1 절", "analysts": []}]}],
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _project(session: AsyncSession, owner_id: uuid.UUID, *, status: str) -> uuid.UUID:
    proj = Project(
        title="설정 동결 테스트",
        topic="테스트 주제",
        preset=None,
        config={"outline": _OUTLINE, "model_mode": "economy"},
        depth_mode="standard",
        owner_id=owner_id,
        status=status,
    )
    session.add(proj)
    await session.flush()
    await session.commit()
    return proj.id


class TestConfigFrozen:
    async def test_archived_project_rejects_config_change(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        """보관본만 동결이다 — 되살릴 일이 없는 기록이라 손대지 않는다."""
        pid = await _project(test_session, worker_user.id, status="archived")
        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _OUTLINE, "model_mode": "premium"}},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "PROJECT_CONFIG_FROZEN"

    async def test_completed_project_accepts_config_change(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        """완료는 더 이상 동결이 아니다(2026-08-25 설계 전환).

        보고서는 완주가 끝이 아니라 품질을 보고 계속 손보는 대상이고, 고친 내용이
        본문에 닿았는지는 '미반영'으로 드러난다(services/sections/drift). 동결의 옛
        근거가 "저장해도 반영될 자리가 없다"였는데, 이제 저장이 실제로 무언가를 한다.
        """
        pid = await _project(test_session, worker_user.id, status="completed")
        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _OUTLINE, "model_mode": "premium"}},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize("status", ["created", "planning", "researching", "writing"])
    async def test_unfinished_project_still_accepts_config_change(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        status: str,
    ) -> None:
        """진행 중·시작 전은 다음 단계가 남아 있으므로 저장이 반영될 자리가 있다."""
        pid = await _project(test_session, worker_user.id, status=status)
        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _OUTLINE, "model_mode": "premium"}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["config"]["model_mode"] == "premium"
