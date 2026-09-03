"""관리자 대리 조작 — 남의 프로젝트 설정 저장이 편집자 기준으로 막히던 자리.

`_get_authorized_project`는 super_admin·admin에게 남의 프로젝트를 열어 준다.
그런데 PATCH /config가 개인 규칙·개인 에이전트를 **편집자** 기준으로 검증해서,
관리자는 열 수는 있는데 저장은 422로 막혔다(2026-08-25 실사고: 빈 절을 고치려고
목차에 에이전트를 배정해 저장 → UNKNOWN_RULE, 소유자 외 전 계정 차단).

config에 실린 규칙·에이전트는 프로젝트 소유자의 것이므로 소유자 스코프로 본다.
편집자가 소유자면 값이 같아 소유자 자신의 계약은 그대로다.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.user import User
from src.db.models.user_prompt import UserPrompt
from tests.conftest import auth_headers as _auth

pytestmark = pytest.mark.asyncio


def _outline(analysts: list[str]) -> dict:
    return {"chapters": [{"title": "1장", "sections": [{"title": "1.1 절", "analysts": analysts}]}]}


async def _rule(session: AsyncSession, owner_id: uuid.UUID) -> uuid.UUID:
    row = UserPrompt(
        owner_id=owner_id,
        kind="rule",
        name="소유자 개인 작성 규칙",
        content="개조식으로 쓴다.",
    )
    session.add(row)
    await session.flush()
    await session.commit()
    return row.id


async def _personal_agent(session: AsyncSession, owner_id: uuid.UUID) -> str:
    """소유자만 아는 개인 에이전트(비공개) — 남의 카탈로그에는 안 뜬다."""
    row = UserPrompt(
        owner_id=owner_id,
        kind="agent",
        name="소유자전용분석",
        content="너는 소유자 전용 분석가다.",
        is_public=False,
    )
    session.add(row)
    await session.flush()
    await session.commit()
    return row.name


async def _project(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    config: dict,
    status: str = "cancelled",
) -> uuid.UUID:
    proj = Project(
        title="관리자 대리 조작 테스트",
        topic="테스트 주제",
        preset=None,
        config=config,
        depth_mode="standard",
        owner_id=owner_id,
        status=status,
    )
    session.add(proj)
    await session.flush()
    await session.commit()
    return proj.id


class TestAdminEditsOthersConfig:
    @pytest.mark.parametrize("actor", ["super_admin", "admin", "owner"])
    async def test_owner_scoped_rule_does_not_block_save(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        worker_user: User,
        worker_token: str,
        admin_token: str,
        super_admin_token: str,
        actor: str,
    ) -> None:
        """소유자의 개인 규칙이 실린 config를 관리자가 저장해도 막히지 않는다."""
        rule_id = await _rule(test_session, worker_user.id)
        pid = await _project(
            test_session,
            worker_user.id,
            config={"outline": _outline([]), "rules": [str(rule_id)]},
        )
        token = {
            "super_admin": super_admin_token,
            "admin": admin_token,
            "owner": worker_token,
        }[actor]

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(token),
            # 실사고와 같은 편집: 빈 절에 에이전트를 배정하고 규칙은 그대로 돌려보낸다.
            json={"config": {"outline": _outline(["정책동향"]), "rules": [str(rule_id)]}},
        )

        assert resp.status_code == 200, resp.text
        saved = resp.json()["config"]["outline"]["chapters"][0]["sections"][0]
        assert saved["analysts"] == ["정책동향"]

    async def test_admin_can_save_owner_personal_analyst(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        worker_user: User,
        super_admin_token: str,
    ) -> None:
        """소유자의 **비공개** 개인 에이전트 배정도 관리자가 그대로 저장할 수 있다.

        편집자 카탈로그로 보면 남의 비공개 에이전트는 '알 수 없는 에이전트'가 되어,
        관리자가 손대지도 않은 배정 때문에 저장 전체가 막혔다.
        """
        name = await _personal_agent(test_session, worker_user.id)
        pid = await _project(test_session, worker_user.id, config={"outline": _outline([name])})

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(super_admin_token),
            json={"config": {"outline": _outline([name]), "model_mode": "premium"}},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["config"]["model_mode"] == "premium"

    async def test_unknown_rule_still_rejected(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        worker_user: User,
        super_admin_token: str,
    ) -> None:
        """소유자 것도 아닌 규칙 id는 여전히 막는다 — 검증을 없앤 게 아니라 임자를 고쳤다."""
        pid = await _project(test_session, worker_user.id, config={"outline": _outline([])})

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(super_admin_token),
            json={"config": {"outline": _outline([]), "rules": [str(uuid.uuid4())]}},
        )

        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "UNKNOWN_RULE"

    async def test_other_worker_still_forbidden(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        worker_token: str,
    ) -> None:
        """접근 가드는 그대로 — 관리자가 아닌 남은 여전히 못 연다."""
        pid = await _project(test_session, admin_user.id, config={"outline": _outline([])})

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline(["정책동향"])}},
        )

        assert resp.status_code == 403, resp.text
