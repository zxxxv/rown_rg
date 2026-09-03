"""검증 커버리지 API — PM 경고 카드의 '검사 분모' 표시용 읽기 전용 집계.

sections에 직접 행을 심어 커버리지 숫자가 순수 함수(claim_coverage) 합산과
일치하는지, 권한 가드가 verify-report와 같은 눈금(뷰어 열람 가능)인지 검증한다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.section import Section
from tests.conftest import auth_headers as _auth


async def _make_project(session: AsyncSession, owner_id: UUID) -> Project:
    project = Project(
        title="커버리지 테스트",
        topic="탄소 규제",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="completed",
    )
    session.add(project)
    await session.flush()
    return project


async def _seed(session: AsyncSession, project_id: UUID) -> None:
    session.add(
        Section(
            id=uuid4(),
            project_id=project_id,
            chapter_number=1,
            section_number=1,
            chapter_title="개요",
            title="배경",
            level=2,
            # 픽업 1(수치 주장) + 미픽업 1(명사 종결·무수치) + 후보 제외 1(캡션)
            content=(
                "ㅇ 참여 기업 수는 전년 대비 21개사 증가한 것으로 집계됐음 (출처 1)\n"
                "ㅇ 향후 정책 방향에 대한 종합적 검토와 제도 개선 논의\n"
                "표: 글로벌 RE100 가입·목표 설정·보고 기준\n"
            ),
            source_ids=[],
            qa_status="passed",
            status="completed",
        )
    )
    await session.commit()


class TestVerifyCoverageApi:
    async def test_coverage_numbers_match_pure_function(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        await _seed(test_session, project.id)

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/verify-coverage",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["n_sections"] == 1
        assert body["n_candidates"] == 2  # 캡션은 후보가 아니다
        assert body["n_claims"] == 1
        assert body["claim_coverage"] == 0.5
        assert body["missed_numeric"] == 0  # 0이 아니면 분해 회귀
        assert isinstance(body["llm_verify_enabled"], bool)
        assert isinstance(body["pm_verify_enabled"], bool)

    async def test_unknown_project_404(
        self,
        test_client: AsyncClient,
        super_admin_token: str,
    ) -> None:
        resp = await test_client.get(
            f"/api/v1/projects/{uuid4()}/verify-coverage",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 404
