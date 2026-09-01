"""자료를 빼면서 근거를 잃은 문단을 화면이 짚을 수 있어야 한다.

자료 제외는 그 자료를 가리키던 마커를 본문에서 지운다(test_citation_rebase). 그 자리의
문장은 주장을 그대로 하면서 근거만 잃는데, **지워진 마커는 흔적을 남기지 않아** 나중에는
어디였는지 알 수 없다. 그래서 지우는 그 순간 자리를 남긴다.

값이 이유다: 절 전체 재작성은 실측 $0.4~$1.3, 그 문단만 고치면 블록 재작성 1콜이다.
어디를 고치면 되는지 모르면 사람은 절 전체를 누른다.
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


# 문단 둘 — 앞 문단만 1번 자료를 인용한다. 뒤 문단은 건드려지면 안 된다.
_BODY = "첫 문단은 1번을 본다 [1].\n\n둘째 문단은 2번만 본다 [2]."


async def _fixture(session: AsyncSession, owner_id: uuid.UUID):
    proj = Project(
        title="근거 잃은 문단",
        topic="주제",
        config={},
        status="completed",
        depth_mode="full_report",
        owner_id=owner_id,
    )
    session.add(proj)
    await session.flush()

    sources, chunks = [], []
    for i in range(2):
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
            content=_BODY,
            source_ids=[c.id for c in chunks],
            meta={"citation_chunks": {str(i + 1): [str(chunks[i].id)] for i in range(2)}},
            status="completed",
        )
    )
    await session.commit()
    return proj.id, sid, sources


class TestLostEvidence:
    async def test_exclusion_records_the_paragraph_that_lost_its_marker(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid, sid, sources = await _fixture(test_session, worker_user.id)

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/sources/{sources[0].id}",
            headers=_auth(worker_token),
            json={"is_included": False},
        )
        assert resp.status_code == 200, resp.text

        body = (
            await test_client.get(
                f"/api/v1/projects/{pid}/sections/{sid}", headers=_auth(worker_token)
            )
        ).json()
        lost = body["evidence_lost"]
        assert len(lost) == 1, f"근거를 잃은 문단은 하나여야 한다: {lost}"
        assert lost[0]["text"] == "첫 문단은 1번을 본다."
        assert lost[0]["n_markers"] == 1
        # 뒤 문단은 번호만 당겨졌을 뿐 근거를 잃지 않았다 — 짚으면 거짓말이다.
        assert "둘째 문단" not in lost[0]["text"]

    async def test_block_rewrite_clears_only_that_paragraph(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """고쳐 놓고도 배지가 남으면 무엇을 더 해야 하는지 알 수 없다(미반영 지문과 한 세트)."""
        from src.api.routers import projects as projects_router

        pid, sid, sources = await _fixture(test_session, worker_user.id)
        await test_client.patch(
            f"/api/v1/projects/{pid}/sources/{sources[0].id}",
            headers=_auth(worker_token),
            json={"is_included": False},
        )

        async def _fake_block_rewriter(_proj, _row, _data) -> str:
            return "첫 문단을 근거 없이 다시 썼다."

        monkeypatch.setattr(projects_router, "_block_rewriter", _fake_block_rewriter)

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/rewrite-block",
            headers=_auth(worker_token),
            json={"block": "첫 문단은 1번을 본다.", "instruction": ""},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["evidence_lost"] == []

    async def test_section_rewrite_clears_every_mark(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """절을 통째로 다시 쓰면 그 문단 자체가 사라진다 — 없는 자리를 짚을 수 없다."""
        from src.api.routers import projects as projects_router
        from src.core.types import SectionDraft

        pid, sid, sources = await _fixture(test_session, worker_user.id)
        await test_client.patch(
            f"/api/v1/projects/{pid}/sources/{sources[0].id}",
            headers=_auth(worker_token),
            json={"is_included": False},
        )

        async def _fake_rewriter(_proj, plan, _instruction) -> SectionDraft:
            return SectionDraft(
                section_id=plan.section_id, content="완전히 새로 쓴 본문.", cited_chunk_ids=[]
            )

        monkeypatch.setattr(projects_router, "_section_rewriter", _fake_rewriter)

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/rewrite",
            headers=_auth(worker_token),
            json={"instruction": ""},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["evidence_lost"] == []
