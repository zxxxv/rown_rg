"""보고서 재개·버전 스냅샷·비교 API — 시차 작성 + 버전 관리 (2026-08-21 설계).

계약:
- reopen은 completed 전용이고, 현재 완성본을 버전으로 얼린 뒤 RESEARCHING으로
  되돌린다(실행은 사람이 시작).
- 스냅샷은 append-only + 내용 지문 중복 방지(같은 내용은 새 버전을 안 만든다).
- diff는 절 안정 id로 맞춘다 — 번호가 밀려도 '수정'과 '이동'을 오판하지 않는다.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.user import User
from src.services.sections.versions import diff_sections, snapshot_report


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _insert_project(session: AsyncSession, owner_id: uuid.UUID, status: str) -> uuid.UUID:
    proj = Project(
        title="버전 테스트",
        topic="주제",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status=status,
    )
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    return proj.id


def _row(project_id, sid, ch, sec, title, content):
    return Section(
        id=sid,
        project_id=project_id,
        chapter_number=ch,
        section_number=sec,
        chapter_title=f"{ch}장",
        title=title,
        level=2,
        content=content,
        source_ids=[],
        meta={},
        qa_status="passed",
        status="completed",
    )


class TestSnapshot:
    async def test_snapshot_dedup_and_increment(
        self, test_session: AsyncSession, worker_user: User
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        sid = uuid.uuid4()
        test_session.add(_row(pid, sid, 1, 1, "가", "본문 v1"))
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="assemble") == 1
        # 같은 내용 재스냅샷 — 새 버전을 만들지 않고 기존 번호를 돌려준다.
        assert await snapshot_report(test_session, pid, reason="reopen") == 1
        row = await test_session.get(Section, sid)
        row.content = "본문 v2"
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="assemble") == 2

    async def test_empty_report_not_snapshotted(
        self, test_session: AsyncSession, worker_user: User
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        test_session.add(_row(pid, uuid.uuid4(), 1, 1, "가", "   "))
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="assemble") is None


class TestReopen:
    async def test_reopen_snapshots_and_reverts_status(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        test_session.add(_row(pid, uuid.uuid4(), 1, 1, "가", "완성 본문"))
        await test_session.commit()
        resp = await test_client.post(f"/api/v1/projects/{pid}/reopen", headers=_auth(worker_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "researching"
        versions = await test_client.get(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token)
        )
        body = versions.json()
        assert len(body) == 1
        assert body[0]["version_no"] == 1
        assert body[0]["reason"] == "reopen"

    async def test_reopen_rejects_non_completed(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "reviewing")
        resp = await test_client.post(f"/api/v1/projects/{pid}/reopen", headers=_auth(worker_token))
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PROJECT_NOT_REOPENABLE"


class TestDiffApi:
    async def test_diff_against_current(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        keep, drop = uuid.uuid4(), uuid.uuid4()
        test_session.add_all(
            [_row(pid, keep, 1, 1, "가", "원본"), _row(pid, drop, 1, 2, "나", "지워질 절")]
        )
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="assemble") == 1
        # 현재 사본을 바꾼다: 수정 + 삭제 + 추가.
        (await test_session.get(Section, keep)).content = "고친 본문"
        await test_session.delete(await test_session.get(Section, drop))
        added = uuid.uuid4()
        test_session.add(_row(pid, added, 2, 1, "다", "새 절"))
        await test_session.commit()
        resp = await test_client.get(
            f"/api/v1/projects/{pid}/versions/diff",
            params={"base": 1},
            headers=_auth(worker_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["n_modified"], body["n_removed"], body["n_added"]) == (1, 1, 1)
        by_id = {e["section_id"]: e for e in body["entries"]}
        assert by_id[str(keep)]["status"] == "modified"
        assert by_id[str(drop)]["status"] == "removed"
        assert by_id[str(added)]["status"] == "added"


class TestDiffSectionsPure:
    def _s(self, sid, ch, sec, title="절", content="본문"):
        return {
            "section_id": str(sid),
            "chapter_number": ch,
            "section_number": sec,
            "chapter_title": f"{ch}장",
            "title": title,
            "content": content,
        }

    def test_renumbered_same_content_is_moved_not_modified(self):
        sid = uuid.uuid4()
        [e] = diff_sections([self._s(sid, 1, 2)], [self._s(sid, 1, 3)])
        assert e["status"] == "unchanged"
        assert e["moved"] is True

    def test_removed_section_kept_in_flow_order(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        base = [self._s(a, 1, 1), self._s(b, 1, 2), self._s(c, 1, 3)]
        target = [self._s(a, 1, 1), self._s(c, 1, 2)]
        entries = diff_sections(base, target)
        assert [e["section_id"] for e in entries] == [str(a), str(b), str(c)]
        assert entries[1]["status"] == "removed"

    def test_title_change_counts_as_modified(self):
        sid = uuid.uuid4()
        [e] = diff_sections([self._s(sid, 1, 1, title="옛")], [self._s(sid, 1, 1, title="새")])
        assert e["status"] == "modified"


class TestFinalize:
    """완성 선언 분리(0045) — completed는 '사이클 완료', 확정은 사람이 누른다."""

    async def test_finalize_sets_flag_and_snapshots(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        test_session.add(_row(pid, uuid.uuid4(), 1, 1, "가", "확정할 본문"))
        await test_session.commit()

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/finalize", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["finalized_at"] is not None
        versions = (
            await test_client.get(f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token))
        ).json()
        assert [v["reason"] for v in versions] == ["finalize"]

        # 재호출은 무해 — 새 버전도, 오류도 없다(멱등).
        again = await test_client.post(
            f"/api/v1/projects/{pid}/finalize", headers=_auth(worker_token)
        )
        assert again.status_code == 200
        versions2 = (
            await test_client.get(f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token))
        ).json()
        assert len(versions2) == 1

    async def test_finalize_rejects_non_completed(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "reviewing")
        resp = await test_client.post(
            f"/api/v1/projects/{pid}/finalize", headers=_auth(worker_token)
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PROJECT_NOT_FINALIZABLE"

    async def test_unfinalize_clears_flag_keeps_versions(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        test_session.add(_row(pid, uuid.uuid4(), 1, 1, "가", "본문"))
        await test_session.commit()
        await test_client.post(f"/api/v1/projects/{pid}/finalize", headers=_auth(worker_token))

        resp = await test_client.delete(
            f"/api/v1/projects/{pid}/finalize", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["finalized_at"] is None
        assert resp.json()["status"] == "completed"
        versions = (
            await test_client.get(f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token))
        ).json()
        assert len(versions) == 1  # 확정 시점 버전은 남는다

    async def test_reopen_clears_finalized(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        test_session.add(_row(pid, uuid.uuid4(), 1, 1, "가", "본문"))
        await test_session.commit()
        await test_client.post(f"/api/v1/projects/{pid}/finalize", headers=_auth(worker_token))

        resp = await test_client.post(f"/api/v1/projects/{pid}/reopen", headers=_auth(worker_token))
        assert resp.status_code == 200, resp.text
        # 다시 열린 보고서는 확정본이 아니다 — 상태 리스너가 선언을 함께 내린다.
        assert resp.json()["finalized_at"] is None


class TestManualVersion:
    """수동 버전 저장 — 자동 트리거 사이의 수동 편집 구간을 보존하는 체크포인트."""

    async def test_manual_snapshot_and_dedup(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        sid = uuid.uuid4()
        test_session.add(_row(pid, sid, 1, 1, "가", "본문 v1"))
        await test_session.commit()

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token), json={}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"version_no": 1, "created": True}

        # 같은 내용 재저장 — 새 버전을 만들지 않는다.
        again = await test_client.post(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token), json={}
        )
        assert again.json() == {"version_no": 1, "created": False}

        # 내용을 고치고 꼬리표를 달아 저장 — reason에 꼬리표가 붙는다.
        row = await test_session.get(Section, sid)
        row.content = "본문 v2"
        await test_session.commit()
        noted = await test_client.post(
            f"/api/v1/projects/{pid}/versions",
            headers=_auth(worker_token),
            json={"note": "표 손보기 전"},
        )
        assert noted.json() == {"version_no": 2, "created": True}
        versions = (
            await test_client.get(f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token))
        ).json()
        assert versions[0]["reason"] == "manual:표 손보기 전"

    async def test_manual_snapshot_rejects_empty_report(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "writing")
        resp = await test_client.post(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token), json={}
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "NOTHING_TO_SNAPSHOT"
