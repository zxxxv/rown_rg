"""시사점 요약 별도 한글 파일 다운로드 — GET /projects/{id}/insights/export.

계약(2026-08-27 결정): 요약은 **본문 HWPX에 실리지 않고** 자기 파일로 나간다.
저장된 요약(projects.insights)에서 그때그때 렌더하므로 '다시 만들기' 결과가 바로
파일에 반영되고, 요약이 없으면 빈 파일 대신 404로 끊는다(받는 사람이 빈 문서를
납품물로 착각하지 않게).
"""

from __future__ import annotations

import uuid
from urllib.parse import unquote

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.user import User

_SUMMARY = "## 핵심 요약\n\n□ 배출권 격차가 비용으로 전가된다\nㅇ 실측 산정 전환이 부담을 낮춘다"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _project_with_summary(
    session: AsyncSession, owner_id: uuid.UUID, insights: dict[str, object] | None
) -> uuid.UUID:
    proj = Project(
        title="탄소규제 대응 전략",
        topic="주제",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="completed",
        insights=insights,
    )
    session.add(proj)
    await session.flush()
    session.add(
        Section(
            id=uuid.uuid4(),
            project_id=proj.id,
            chapter_number=6,
            section_number=2,
            chapter_title="6장",
            title="시사점 및 제언",
            level=2,
            content="ㅇ 결론 본문",
            source_ids=[],
            meta={},
            qa_status="passed",
            status="completed",
        )
    )
    await session.commit()
    return proj.id


class TestInsightsExport:
    async def test_downloads_hwpx_of_stored_summary(
        self,
        test_session: AsyncSession,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "export_dir", str(tmp_path))
        pid = await _project_with_summary(
            test_session, worker_user.id, {"content": _SUMMARY, "source_sections": ["6.2 시사점"]}
        )

        resp = await test_client.get(
            f"/api/v1/projects/{pid}/insights/export", headers=_auth(worker_token)
        )

        assert resp.status_code == 200
        # HWPX는 zip 컨테이너다 — 껍데기가 아니라 진짜 문서가 내려왔는지 시그니처로 본다.
        assert resp.content[:2] == b"PK"
        assert len(resp.content) > 1000
        # 파일명은 RFC 5987로 퍼센트 인코딩돼 내려간다 — 풀어서 본다.
        assert "탄소규제 대응 전략 시사점 요약.hwpx" in unquote(resp.headers["content-disposition"])
        # 본문 완성본과 섞이지 않게 하위 폴더에 남는다.
        assert list((tmp_path / "_insights").glob("*.hwpx"))

    async def test_404_when_no_summary_yet(
        self,
        test_session: AsyncSession,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        tmp_path,
        monkeypatch,
    ):
        """조립 전이거나 생성 실패 — 빈 문서를 내주는 대신 이유를 코드로 알린다."""
        monkeypatch.setattr(settings, "export_dir", str(tmp_path))
        pid = await _project_with_summary(test_session, worker_user.id, None)

        resp = await test_client.get(
            f"/api/v1/projects/{pid}/insights/export", headers=_auth(worker_token)
        )

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "INSIGHTS_NOT_READY"
