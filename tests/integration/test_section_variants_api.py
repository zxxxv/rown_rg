"""한 절을 여러 벌 뽑아 놓고 사람이 고른다.

재작성은 한 번에 하나만 준다. 마음에 안 들면 다시 눌러야 하고, 그러면 방금 것은
사라진다 — 둘을 나란히 놓고 고를 수가 없었다. 실제로 사람이 하는 일은 "이게 나은가
저게 나은가"인데 화면이 그걸 못 하게 막고 있었다(2026-08-26).

값이 안 개수에 곱해지므로(절당 실측 $0.4~$1.3) 기본 3·최대 4로 묶는다.
"""

from __future__ import annotations

import asyncio
import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.section import Section
from src.db.models.user import User
from tests.integration.test_drift_api import _auth, _completed_project


async def _wait_done(pid: uuid.UUID) -> None:
    """끝날 때까지 기다린다 — 다 돈 작업을 **지우지는 않는다**.

    실패 사유는 작업 상태에만 산다(안 자체는 절 meta에 쌓인다). 여기서 지우면 화면이
    읽기 전에 사라져, "셋 중 둘만 나왔다"는 사실을 아무도 모르게 된다. 실서비스에서도
    다음 뽑기 전까지 남아 있는 것이 계약이다.
    """
    from src.api.routers import projects as projects_router
    from src.services.jobs import get_job

    for _ in range(100):
        job = get_job(pid, projects_router.VARIANTS_JOB)
        if job and not job.running:
            return
        await asyncio.sleep(0.1)


class TestSectionVariants:
    async def test_three_variants_pile_up_and_one_gets_adopted(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        from src.api.routers import projects as projects_router
        from src.core.types import SectionDraft

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")

        calls = {"n": 0}

        async def _fake_rewriter(_proj, plan, _instruction) -> SectionDraft:
            calls["n"] += 1
            return SectionDraft(
                section_id=plan.section_id,
                content=f"{calls['n']}번째 안의 본문입니다.",
                cited_chunk_ids=[],
                pool_chunk_ids=[uuid.uuid4()],
            )

        monkeypatch.setattr(projects_router, "_section_rewriter", _fake_rewriter)

        start = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/variants",
            headers=_auth(worker_token),
            json={"n": 3, "instruction": ""},
        )
        assert start.status_code == 202, start.text
        await _wait_done(pid)

        body = (
            await test_client.get(
                f"/api/v1/projects/{pid}/sections/{sid}/variants", headers=_auth(worker_token)
            )
        ).json()
        assert body["running"] is False
        assert len(body["variants"]) == 3, body
        assert [v["content"] for v in body["variants"]] == [
            "1번째 안의 본문입니다.",
            "2번째 안의 본문입니다.",
            "3번째 안의 본문입니다.",
        ]
        # 고르는 데 필요한 사실이 함께 온다 - 길이·인용·근거 수.
        assert body["variants"][0]["n_chars"] == len("1번째 안의 본문입니다.")
        assert body["variants"][0]["evidence_count"] == 1

        # 본문은 아직 그대로다 - 뽑기와 적용은 다른 행동이다.
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "이미 쓰인 본문입니다."

        picked = body["variants"][1]
        adopt = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/variants/{picked['id']}/adopt",
            headers=_auth(worker_token),
        )
        assert adopt.status_code == 200, adopt.text
        assert adopt.json()["content"] == "2번째 안의 본문입니다."

        await test_session.refresh(row)
        assert row.content == "2번째 안의 본문입니다."
        # 고른 뒤에는 나머지가 남지 않는다 - 본문이 된 안 옆의 후보는 "아직 안 골랐다"로 읽힌다.
        after = (
            await test_client.get(
                f"/api/v1/projects/{pid}/sections/{sid}/variants", headers=_auth(worker_token)
            )
        ).json()
        assert after["variants"] == []

        # 재작성과 같은 반영 경로를 지났는가 - 버전이 얼려져야 한다(되돌릴 지점).
        versions = (
            await test_client.get(f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token))
        ).json()
        assert any(v["reason"].startswith("rewrite:") for v in versions)

    async def test_a_failed_variant_does_not_kill_the_rest(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """셋 중 둘만 나와도 고를 수 있다 - 하나의 실패로 전부를 잃으면 값만 태운다."""
        from src.api.routers import projects as projects_router
        from src.core.types import SectionDraft

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        calls = {"n": 0}

        async def _flaky(_proj, plan, _instruction) -> SectionDraft:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("모델 호출 실패")
            return SectionDraft(
                section_id=plan.section_id, content=f"안 {calls['n']}", cited_chunk_ids=[]
            )

        monkeypatch.setattr(projects_router, "_section_rewriter", _flaky)

        await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/variants",
            headers=_auth(worker_token),
            json={"n": 3},
        )
        await _wait_done(pid)

        body = (
            await test_client.get(
                f"/api/v1/projects/{pid}/sections/{sid}/variants", headers=_auth(worker_token)
            )
        ).json()
        assert len(body["variants"]) == 2
        assert "2번째 안" in body["failures"]

    async def test_locked_section_refuses_to_pull_variants(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """잠금은 AI 경로 전부를 막는다 - 여기만 열려 있으면 자물쇠가 아니다."""
        from src.api.routers import projects as projects_router

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")

        async def _boom(*_a, **_k):
            raise AssertionError("잠긴 절에 모델을 불렀다")

        monkeypatch.setattr(projects_router, "_section_rewriter", _boom)
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}/lock",
            headers=_auth(worker_token),
            json={"locked": True},
        )

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/variants",
            headers=_auth(worker_token),
            json={"n": 3},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "SECTION_LOCKED"

    async def test_discard_leaves_the_body_alone(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        from src.api.routers import projects as projects_router
        from src.core.types import SectionDraft

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")

        async def _fake(_proj, plan, _i) -> SectionDraft:
            return SectionDraft(section_id=plan.section_id, content="버릴 안", cited_chunk_ids=[])

        monkeypatch.setattr(projects_router, "_section_rewriter", _fake)
        await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/variants",
            headers=_auth(worker_token),
            json={"n": 2},
        )
        await _wait_done(pid)

        resp = await test_client.delete(
            f"/api/v1/projects/{pid}/sections/{sid}/variants", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        body = (
            await test_client.get(
                f"/api/v1/projects/{pid}/sections/{sid}/variants", headers=_auth(worker_token)
            )
        ).json()
        assert body["variants"] == []
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "이미 쓰인 본문입니다."

    async def test_n_is_capped(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """값이 개수에 곱해지므로 상한을 스키마가 지킨다 - 10안은 실측 $13짜리 실수다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        resp = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/variants",
            headers=_auth(worker_token),
            json={"n": 10},
        )
        assert resp.status_code == 422
