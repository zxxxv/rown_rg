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
from tests.conftest import auth_headers as _auth


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
        # 재개가 서는 자리는 '검토'다 — 본문이 이미 있으니까(2026-08-26 단계 파생화).
        # 예전엔 RESEARCHING을 박아 넣었는데, 그 값이 '수집 실행 중'의 표시 상태이기도
        # 해서 자료 화면이 스피너를 걸고 끝나지 않았다. 이제 단계는 산출물이 답한다.
        assert resp.json()["status"] == "reviewing"
        versions = await test_client.get(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token)
        )
        body = versions.json()
        assert len(body) == 1
        assert body[0]["version_no"] == 1
        assert body[0]["reason"] == "reopen"

    async def test_reopen_opens_source_review_gate(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """재개는 자료 검토 게이트를 연다 — status만 되돌리면 화면이 거짓말을 한다.

        RESEARCHING은 '수집 실행 중'의 표시 상태이기도 해서, 게이트가 없으면 자료
        화면이 status만 보고 "AI가 자료를 검색하고 있습니다"를 띄운다(실제로는 아무
        것도 안 돌아 끝나지도 않는다 — 2026-08-25 사용자 보고).
        """
        pid = await _insert_project(test_session, worker_user.id, "completed")
        test_session.add(_row(pid, uuid.uuid4(), 1, 1, "가", "완성 본문"))
        await test_session.commit()

        resp = await test_client.post(f"/api/v1/projects/{pid}/reopen", headers=_auth(worker_token))
        assert resp.status_code == 200, resp.text

        progress = await test_client.get(
            f"/api/v1/projects/{pid}/progress", headers=_auth(worker_token)
        )
        gate = progress.json()["pending_gate"]
        assert gate is not None, "재개 후 자료 검토 게이트가 열려 있어야 한다"
        assert gate["gate"] == "source_pool"
        # 화면이 '첫 수집 직후'와 구분해 말할 수 있도록 표식을 싣는다.
        assert gate["payload"]["reopened"] is True
        assert "다시 열었습니다" in gate["payload"]["message"]

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


class TestSectionHistory:
    """절 쪽에서 들어가는 버전 이력 — 같은 내용 구간은 한 칸으로 접힌다."""

    async def test_history_collapses_unchanged_runs(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        mine, other = uuid.uuid4(), uuid.uuid4()
        test_session.add_all(
            [_row(pid, mine, 3, 2, "편익 추정", "본문 A"), _row(pid, other, 1, 1, "개요", "가")]
        )
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="assemble") == 1
        # 남의 절만 고친 버전 두 개 — 내 절 이력에는 한 칸도 늘지 않아야 한다.
        for i, text in enumerate(("나", "다"), start=2):
            (await test_session.get(Section, other)).content = text
            await test_session.flush()
            assert await snapshot_report(test_session, pid, reason="edit:1.1") == i
        # 이제 내 절을 고친다.
        (await test_session.get(Section, mine)).content = "본문 B"
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="rewrite:3.2") == 4
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{pid}/sections/{mine}/history", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        # A(v1~v3) · B(v4) — 남의 절만 바뀐 v2·v3는 접힌다.
        assert [(e["version_no"], e["until_version"]) for e in entries] == [(4, 4), (1, 3)]
        assert entries[0]["content"] == "본문 B"
        assert entries[0]["is_current"] is True
        assert entries[1]["content"] == "본문 A"
        assert entries[1]["is_current"] is False

    async def test_history_skips_versions_without_the_section(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        first = uuid.uuid4()
        test_session.add(_row(pid, first, 1, 1, "가", "처음"))
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="assemble") == 1
        later = uuid.uuid4()
        test_session.add(_row(pid, later, 1, 2, "나중에 생긴 절", "새 본문"))
        await test_session.flush()
        assert await snapshot_report(test_session, pid, reason="outline") == 2
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{pid}/sections/{later}/history", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        assert [e["version_no"] for e in entries] == [2]


class TestVersionListMetadata:
    async def test_list_carries_author_and_delta(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _insert_project(test_session, worker_user.id, "completed")
        sid = uuid.uuid4()
        test_session.add(_row(pid, sid, 1, 1, "가", "짧은 본문"))
        await test_session.commit()
        await test_client.post(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token), json={}
        )
        row = await test_session.get(Section, sid)
        row.content = "짧은 본문에 더 붙인 긴 본문"
        await test_session.commit()
        await test_client.post(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token), json={}
        )

        versions = (
            await test_client.get(f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token))
        ).json()
        assert versions[0]["created_by_name"] == worker_user.name
        assert versions[0]["delta_chars"] == len("짧은 본문에 더 붙인 긴 본문") - len("짧은 본문")
        assert versions[0]["n_changed_sections"] == 1
        # 첫 버전은 견줄 앞이 없다.
        assert versions[1]["delta_chars"] is None
        assert versions[1]["n_changed_sections"] is None


class TestVerifyStaleness:
    """PM 검증 경고가 지금 본문에 대한 판정인지 — 절을 고치면 낡았다고 말해야 한다."""

    async def test_stale_flips_after_editing_a_section(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        from src.services.qa.pm_verify import persist_findings

        pid = await _insert_project(test_session, worker_user.id, "completed")
        sid = uuid.uuid4()
        test_session.add(_row(pid, sid, 1, 1, "가", "검증 대상 본문"))
        await test_session.commit()

        before = (
            await test_client.get(
                f"/api/v1/projects/{pid}/verify-report/status", headers=_auth(worker_token)
            )
        ).json()
        # 검증 전에는 모른다 — 모르는 것을 낡았다고 하지 않는다.
        assert before["stale"] is False and before["verified_at"] is None

        await persist_findings(pid, [])
        fresh = (
            await test_client.get(
                f"/api/v1/projects/{pid}/verify-report/status", headers=_auth(worker_token)
            )
        ).json()
        assert fresh["stale"] is False and fresh["verified_at"] is not None

        row = await test_session.get(Section, sid)
        row.content = "사람이 고친 본문"
        await test_session.commit()
        stale = (
            await test_client.get(
                f"/api/v1/projects/{pid}/verify-report/status", headers=_auth(worker_token)
            )
        ).json()
        assert stale["stale"] is True
