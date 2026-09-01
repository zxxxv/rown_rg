"""절 잠금(0048)과 미반영 무시 — 진화하는 보고서의 안전장치 두 개.

둘 다 "AI가 사람의 판단을 덮어쓰지 않게" 하는 장치다. 잠금은 손본 절을 재작성에서
빼고, 무시는 "이 어긋남은 괜찮다"는 판단을 기록해 배지를 지운다. 미반영 판정 자체는
test_drift_api가 지키므로, 여기서는 두 장치가 실제로 실행을 막는지만 본다.
"""

from __future__ import annotations

import asyncio
import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.section import Section
from src.db.models.user import User
from tests.integration.test_drift_api import _auth, _completed_project, _outline


class TestSectionLock:
    """잠근 절은 AI가 못 건드린다.

    묶음 재작성은 수십 절을 한 번에 갈아엎는다(전체 실측 $15.5). 공들여 손본 절이
    거기 섞이면 그 손질이 통째로 사라진다 — 버전으로 되돌릴 수는 있어도 어느 절이
    덮였는지 찾는 건 사람 몫이라, 사고를 일어나기 전에 막는 자물쇠가 필요하다.
    """

    async def test_locked_section_refuses_ai_rewrite(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        from src.api.routers import projects as projects_router

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")

        async def _boom(*_a, **_k):
            raise AssertionError("잠긴 절에 모델을 불렀다")

        monkeypatch.setattr(projects_router, "_section_rewriter", _boom)

        lock = await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}/lock",
            headers=_auth(worker_token),
            json={"locked": True},
        )
        assert lock.status_code == 200, lock.text
        assert lock.json()["locked"] is True

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/rewrite",
            headers=_auth(worker_token),
            json={"instruction": ""},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "SECTION_LOCKED"

        # 본문은 그대로 — 잠금은 지키는 것이지 바꾸는 게 아니다.
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "이미 쓰인 본문입니다."

    async def test_human_edit_still_allowed_on_locked_section(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """잠금은 AI를 막는 것이지 사람을 막는 게 아니다 — 잠근 사람이 그 사람이다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}/lock",
            headers=_auth(worker_token),
            json={"locked": True},
        )
        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}",
            headers=_auth(worker_token),
            json={"content": "사람이 직접 고친 본문"},
        )
        assert resp.status_code == 200, resp.text
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "사람이 직접 고친 본문"

    async def test_batch_rewrite_drops_locked_sections_before_starting(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """잠긴 절은 **시작 전에** 덜어낸다.

        실행 중에 거르면 진행률 분모가 부풀어 "2절 중 1절"로 끝나고, 사람은 그걸
        실패로 읽는다.
        """
        from src.api.routers import projects as projects_router
        from src.core.types import SectionDraft
        from src.services.jobs import clear_job, get_job

        locked_id = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, locked_id, "원래 방향")
        free_id = uuid.uuid4()
        test_session.add(
            Section(
                id=free_id,
                project_id=pid,
                chapter_number=1,
                section_number=2,
                chapter_title="1장",
                title="다른 절",
                content="여기는 다시 써도 된다.",
                source_ids=[],
                status="completed",
            )
        )
        await test_session.commit()
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{locked_id}/lock",
            headers=_auth(worker_token),
            json={"locked": True},
        )

        written: list[uuid.UUID] = []

        async def _fake_rewriter(_proj, plan, _instruction) -> SectionDraft:
            written.append(plan.section_id)
            return SectionDraft(section_id=plan.section_id, content="새 본문", cited_chunk_ids=[])

        monkeypatch.setattr(projects_router, "_section_rewriter", _fake_rewriter)

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/rewrite-batch",
            headers=_auth(worker_token),
            json={"section_ids": [str(locked_id), str(free_id)]},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["total"] == 1, "잠긴 절이 분모에 남으면 안 된다"
        assert body["skipped_locked"] == [str(locked_id)]

        for _ in range(80):
            job = get_job(pid, projects_router.REWRITE_JOB)
            if job and not job.running:
                break
            await asyncio.sleep(0.1)
        clear_job(pid, projects_router.REWRITE_JOB)

        assert written == [free_id], "잠긴 절에 모델이 불렸다"
        row = await test_session.get(Section, locked_id)
        await test_session.refresh(row)
        assert row.content == "이미 쓰인 본문입니다."

    async def test_batch_rewrite_refuses_when_everything_is_locked(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}/lock",
            headers=_auth(worker_token),
            json={"locked": True},
        )
        resp = await test_client.post(
            f"/api/v1/projects/{pid}/rewrite-batch",
            headers=_auth(worker_token),
            json={"section_ids": [str(sid)]},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "ALL_SECTIONS_LOCKED"

    async def test_drift_reports_lock_so_the_screen_can_grey_it_out(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """잠갔다고 미반영이 사라지지는 않는다 — 사실은 사실이고, 고를 수만 없다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}/lock",
            headers=_auth(worker_token),
            json={"locked": True},
        )
        await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("완전히 다른 방향", sid)}},
        )
        body = (
            await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        ).json()
        assert body["n_plan_changed"] == 1
        assert body["sections"][0]["locked"] is True


class TestDriftDismiss:
    """미반영 무시 — "이 절은 이대로 둔다"는 선언(2026-08-26).

    미반영은 "다시 써야 한다"가 아니라 "계약이 바뀌었다"는 사실이다. 오탈자 하나 고친
    뒤라면 본문은 이미 새 계약대로인 경우가 많은데, 표시가 남아 있으면 배지가 소음이
    되고 소음이 된 배지는 진짜 미반영도 못 보게 만든다.
    """

    async def test_dismiss_clears_the_mark_without_touching_content(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("완전히 다른 방향", sid)}},
        )
        before = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert before.json()["n_plan_changed"] == 1

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/drift/dismiss",
            headers=_auth(worker_token),
            json={"section_ids": [str(sid)]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["dismissed"] == ["1.1 배경"]

        after = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert after.json()["sections"] == []
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "이미 쓰인 본문입니다.", "무시는 본문을 건드리지 않는다"

    async def test_a_later_outline_change_surfaces_again(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """무시는 영구 침묵이 아니다 — 지금 것만 넘긴다.

        지문을 찍는 방식이라 다음에 계획이 또 바뀌면 저절로 다시 뜬다. 무시 플래그를
        따로 뒀다면 두 번째 변경이 조용히 묻혔을 것이다.
        """
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("두 번째 방향", sid)}},
        )
        await test_client.post(
            f"/api/v1/projects/{pid}/drift/dismiss",
            headers=_auth(worker_token),
            json={"section_ids": [str(sid)]},
        )
        await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("세 번째 방향", sid)}},
        )
        again = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert again.json()["n_plan_changed"] == 1

    async def test_empty_section_cannot_be_dismissed(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """본문 없음은 무시할 수 없다 — 없는 것을 있다고 선언할 수는 없다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        row = await test_session.get(Section, sid)
        row.content = ""
        await test_session.commit()

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/drift/dismiss",
            headers=_auth(worker_token),
            json={"section_ids": [str(sid)]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"dismissed": [], "skipped": ["1.1 배경"]}
        after = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert after.json()["n_missing"] == 1
