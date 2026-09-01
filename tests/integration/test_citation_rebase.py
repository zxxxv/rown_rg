"""자료를 채택에서 빼면 본문 인용 번호가 따라와야 한다.

전역 번호 = 채택 자료의 수집 순서라, 하나를 빼면 그 뒤가 통째로 당겨진다. 참고문헌은
다운로드 시점에 다시 매겨지는데 본문 마커는 안 매겨져서, 고치지 않으면 **문서 전체
인용이 한 칸씩 어긋난 HWPX**가 나온다. 완료 보고서에서 자료를 뺄 수 있게 되면서
(2026-08-26) 실제로 도달 가능해진 경로다.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.section import Section
from src.db.models.user import User


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _fixture(session: AsyncSession, owner_id: uuid.UUID):
    """자료 3건(=전역 1·2·3)과 그 셋을 모두 인용한 절 하나."""
    proj = Project(
        title="인용 재기준",
        topic="주제",
        config={},
        status="completed",
        depth_mode="full_report",
        owner_id=owner_id,
    )
    session.add(proj)
    await session.flush()

    sources, chunks = [], []
    for i in range(3):
        src = ProjectSource(
            project_id=proj.id,
            source_type="web_search",
            title=f"자료{i + 1}",
            url=f"https://e.com/{i}",
            is_included=True,
        )
        session.add(src)
        await session.flush()
        ch = Chunk(project_id=proj.id, source_id=src.id, track="content", content=f"본문{i}")
        session.add(ch)
        await session.flush()
        sources.append(src)
        chunks.append(ch)

    sid = uuid.uuid4()
    session.add(
        Section(
            id=sid,
            project_id=proj.id,
            chapter_number=1,
            section_number=1,
            chapter_title="1장",
            title="배경",
            content="첫 문장 [1]. 둘째 문장 [2]. 셋째 문장 [3].",
            source_ids=[c.id for c in chunks],
            meta={"citation_chunks": {str(i + 1): [str(chunks[i].id)] for i in range(3)}},
            status="completed",
        )
    )
    await session.commit()
    return proj.id, sid, sources


class TestCitationRebase:
    async def test_excluding_a_source_shifts_body_markers(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid, sid, sources = await _fixture(test_session, worker_user.id)

        # 1번 자료를 제외 → 2·3번이 1·2번으로 당겨진다.
        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/sources/{sources[0].id}",
            headers=_auth(worker_token),
            json={"is_included": False},
        )
        assert resp.status_code == 200, resp.text

        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        # [1]은 사라지고(뺀 자료), [2]→[1], [3]→[2]
        assert row.content == "첫 문장. 둘째 문장 [1]. 셋째 문장 [2].", row.content
        assert set(row.meta["citation_chunks"]) == {"1", "2"}

    async def test_rebase_is_idempotent(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """전역→전역 재매핑이라 같은 조건에서 몇 번 돌려도 결과가 같아야 한다."""
        from src.services.sections.renumber import rebase_global_numbers

        pid, sid, sources = await _fixture(test_session, worker_user.id)
        await test_client.patch(
            f"/api/v1/projects/{pid}/sources/{sources[0].id}",
            headers=_auth(worker_token),
            json={"is_included": False},
        )
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        once = row.content

        assert await rebase_global_numbers(test_session, pid) == 0, "이미 맞으면 안 건드린다"
        await test_session.refresh(row)
        assert row.content == once

    async def test_untouched_report_is_not_rewritten(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """마지막 자료를 빼면 앞 번호는 그대로 — 멀쩡한 절을 건드리지 않는다."""
        pid, sid, sources = await _fixture(test_session, worker_user.id)
        await test_client.patch(
            f"/api/v1/projects/{pid}/sources/{sources[2].id}",
            headers=_auth(worker_token),
            json={"is_included": False},
        )
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "첫 문장 [1]. 둘째 문장 [2]. 셋째 문장."
