"""같은 자료 흡수 — 무근거 수치의 마지막 계단 앞 단(2026-08-27).

사람에게 (출처 13)은 "이 자료"지 "이 청크"가 아니다. 청크 단위 인용의 구현 사정
때문에, 인용된 청크엔 없지만 같은 자료의 설문 표에는 버젓이 있는 수치(실측: 610개사
표의 19.8%·17.0%…)가 빨간 무근거 경고를 받았다. v6 실측: 무근거 629→367(42% 흡수).

계약:
  ① 인용 자료의 다른 청크에 있으면 → 무근거에서 빠지고 위치(NumberSpan)를 얻는다
  ② 다른 자료에만 있으면 → 그대로 무근거(오귀속 제안은 relocations 몫)
  ③ 매칭은 자릿수 경계 - "61"이 "610" 안에 걸려 승격되면 안 된다
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.services.qa.alignment import align_section
from src.services.qa.evidence_findings import absorb_same_source_numbers, elsewhere_numbers


@pytest.fixture(autouse=True)
def _wire_session_maker(monkeypatch, test_session_maker):
    """evidence_findings는 세션메이커를 모듈 수준에서 물고 있어 conftest의
    src.db.session 패치가 안 닿는다 - 이 참조를 직접 테스트 DB로 돌린다."""
    # open_session()이 호출 시점에 중앙 전역을 찾으므로 이 한 줄이면 된다
    # (모듈 상단 바인딩 6곳을 걷어낸 2026-09-03 통일에 맞춤).
    monkeypatch.setattr("src.db.session.async_session_maker", test_session_maker)


async def _project_with_source(session: AsyncSession, owner_id) -> tuple[Project, ProjectSource]:
    project = Project(
        title="같은 자료 흡수",
        topic="RE100",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="completed",
    )
    session.add(project)
    await session.flush()
    source = ProjectSource(
        project_id=project.id, source_type="upload", title="실태조사.pdf", url=None
    )
    session.add(source)
    await session.flush()
    return project, source


def _chunk(project_id, source_id, content: str, index: int) -> Chunk:
    return Chunk(
        project_id=project_id,
        source_id=source_id,
        track="content",
        content=content,
        embedding=[0.0] * 1024,
        chunk_index=index,
    )


class TestAbsorbSameSource:
    async def test_number_in_sibling_chunk_absorbed_with_span(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        project, source = await _project_with_source(test_session, super_admin_user.id)
        cited = _chunk(project.id, source.id, "조사 개요와 방법론을 설명하는 대목이다.", 0)
        table = _chunk(project.id, source.id, "업종별 분포: 전기/전자 19.8%, 기계 17.0% 순이다.", 1)
        test_session.add_all([cited, table])
        await test_session.commit()

        claim = "응답 기업 중 전기/전자가 19.8%로 가장 큰 비중을 차지함 (출처 1)"
        (result,) = align_section(claim, {cited.id: cited.content}, {1: [cited.id]})
        assert result.ungrounded == ["19.8%"]

        moved = await absorb_same_source_numbers(project.id, [result])
        assert moved == 1
        assert result.ungrounded == []
        (span,) = [g for g in result.grounded if g.token == "19.8%"]
        # 위치는 원문 청크 기준 - 뷰어가 그 대목으로 점프한다.
        assert span.chunk_id == table.id
        assert "19.8" in table.content[span.start : span.end + 20]

    async def test_number_only_in_other_source_stays_ungrounded(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        project, source = await _project_with_source(test_session, super_admin_user.id)
        other = ProjectSource(
            project_id=project.id, source_type="upload", title="딴 자료.pdf", url=None
        )
        test_session.add(other)
        await test_session.flush()
        cited = _chunk(project.id, source.id, "인용된 자료에는 그 수치가 없다.", 0)
        elsewhere = _chunk(project.id, other.id, "딴 자료에는 37.4%가 있다.", 0)
        test_session.add_all([cited, elsewhere])
        await test_session.commit()

        claim = "해당 비중은 37.4%로 집계됨 (출처 1)"
        (result,) = align_section(claim, {cited.id: cited.content}, {1: [cited.id]})
        assert result.ungrounded == ["37.4%"]
        moved = await absorb_same_source_numbers(project.id, [result])
        # 같은 자료가 아니면 흡수하지 않는다 - 무근거는 유지하되, 절 밖 탐색이
        # 자료 제목·원문 위치를 사람에게 준다(사다리의 마지막 칸).
        assert moved == 0
        assert result.ungrounded == ["37.4%"]
        found = await elsewhere_numbers(project.id, [result])
        ((token, sid, title, cid, st, en),) = found[0]
        assert (token, sid, title, cid) == ("37.4%", other.id, "딴 자료.pdf", elsewhere.id)
        assert "37.4" in elsewhere.content[st : en + 10]

    async def test_digit_boundary_no_false_absorption(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        project, source = await _project_with_source(test_session, super_admin_user.id)
        cited = _chunk(project.id, source.id, "본문 대목이다.", 0)
        sibling = _chunk(project.id, source.id, "표본은 총 6104명이었다.", 1)
        test_session.add_all([cited, sibling])
        await test_session.commit()

        claim = "응답 기업은 610개사로 집계됨 (출처 1)"
        (result,) = align_section(claim, {cited.id: cited.content}, {1: [cited.id]})
        assert result.ungrounded == ["610"]
        moved = await absorb_same_source_numbers(project.id, [result])
        # "610"이 "6104" 안의 토막으로 승격되면 안 된다.
        assert moved == 0
        assert result.ungrounded == ["610"]
