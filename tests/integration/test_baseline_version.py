"""첫 수정이 원본을 지우던 구멍 — 덮어쓰기 전 원본 스냅샷(2026-08-27).

재작성·편집·블록 수정은 모두 '성공 직후'를 버전으로 얼린다. 거기엔 "고치기 전은 직전
버전이 이미 들고 있다"는 전제가 깔려 있었는데, 그 전제가 안 맞는 문서가 실제로 있었다
(실측 18건 중 10건이 본문은 있는데 버전이 0). 그런 문서는 첫 수정이 원본을 통째로
지웠고, 화면은 그동안 "이전 본문은 버전 기록에 남아 있습니다"라고 말하고 있었다.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.report_version import ReportVersion
from src.db.models.section import Section
from src.db.models.user import User
from tests.integration.test_drift_api import _auth, _completed_project

ORIGINAL = "이미 쓰인 본문입니다."


async def _versions(session: AsyncSession, pid: uuid.UUID) -> list[ReportVersion]:
    rows = (
        await session.execute(
            select(ReportVersion)
            .where(ReportVersion.project_id == pid)
            .order_by(ReportVersion.version_no)
        )
    ).scalars()
    return list(rows)


class TestBaselineVersion:
    async def test_manual_edit_keeps_the_original(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """버전이 없는 문서를 손으로 고쳐도 원본이 남는다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        assert await _versions(test_session, pid) == [], "이 검사는 버전 0에서 출발해야 한다"

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}",
            headers=_auth(worker_token),
            json={"content": "손으로 완전히 새로 쓴 본문."},
        )
        assert resp.status_code == 200, resp.text

        versions = await _versions(test_session, pid)
        assert len(versions) == 2, f"원본+수정 두 벌이어야 한다: {[v.reason for v in versions]}"
        assert versions[0].reason == "baseline"
        first = {s["section_id"]: s["content"] for s in versions[0].sections}
        assert first[str(sid)] == ORIGINAL, "원본이 버전에 안 남았다 - 되돌릴 길이 없다"
        last = {s["section_id"]: s["content"] for s in versions[-1].sections}
        assert last[str(sid)] == "손으로 완전히 새로 쓴 본문."

    async def test_existing_version_is_not_duplicated(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """이미 버전이 있으면 원본 스냅샷을 새로 만들지 않는다 - 직전 버전이 곧 원본이다."""
        from src.services.sections.versions import snapshot_report

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        await snapshot_report(test_session, pid, reason="assemble")
        await test_session.commit()
        assert len(await _versions(test_session, pid)) == 1

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}",
            headers=_auth(worker_token),
            json={"content": "손으로 고친 본문."},
        )
        assert resp.status_code == 200, resp.text

        versions = await _versions(test_session, pid)
        reasons = [v.reason for v in versions]
        assert reasons == ["assemble", "edit:1.1"], f"원본이 중복으로 쌓였다: {reasons}"

    async def test_adopting_a_variant_keeps_the_original(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """안 채택도 덮어쓰기다 - 화면이 약속한 '이전 본문은 버전에 남습니다'를 지킨다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        row = await test_session.get(Section, sid)
        vid = str(uuid.uuid4())
        row.meta = {
            **(row.meta or {}),
            "variants": [
                {
                    "id": vid,
                    "content": "고른 안의 본문.",
                    "cited_chunk_ids": [],
                    "pool_chunk_ids": [],
                }
            ],
        }
        await test_session.commit()

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/variants/{vid}/adopt",
            headers=_auth(worker_token),
            json={},
        )
        assert resp.status_code == 200, resp.text

        versions = await _versions(test_session, pid)
        assert versions and versions[0].reason == "baseline", (
            f"채택 전 본문이 안 남았다: {[v.reason for v in versions]}"
        )
        first = {s["section_id"]: s["content"] for s in versions[0].sections}
        assert first[str(sid)] == ORIGINAL
