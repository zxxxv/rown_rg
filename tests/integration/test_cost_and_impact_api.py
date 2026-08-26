"""값을 누르기 전에 안다 — 절당 비용 실측과 자료 제외 영향.

둘 다 "되돌리기 비싼 행동을 하기 전에 값을 보여준다"는 한 가지 목적이다. 절 재작성은
실측 $0.4~$1.3, 자료 제외는 그 자료를 인용한 절 전부를 다시 써야 되돌아온다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.section import Section
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _project(session: AsyncSession, owner_id: uuid.UUID, *, model_mode="standard") -> Project:
    proj = Project(
        title="비용 미리보기",
        topic="주제",
        config={"model_mode": model_mode},
        status="completed",
        depth_mode="full_report",
        owner_id=owner_id,
    )
    session.add(proj)
    await session.flush()
    return proj


def _usage(project_id: uuid.UUID, operation: str, cost: str) -> TokenUsage:
    return TokenUsage(
        project_id=project_id,
        model="claude-sonnet-5",
        operation=operation,
        input_tokens=1000,
        output_tokens=500,
        cost_usd=Decimal(cost),
        mode="live",
    )


class TestCostBasis:
    async def test_per_section_folds_calls_not_rows(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """절 하나에 LLM 콜이 여러 번 나간다 — 행 수로 나누면 값이 몇 배 낮아진다.

        실측(2026-08-26 운영 DB): 20절짜리 보고서에 section_write 행이 236개였다.
        행으로 나누면 절당 $0.11, 절로 접으면 $1.34 — 12배 차이다.
        """
        proj = await _project(test_session, worker_user.id)
        # 2개 절, 콜은 5번. 합계 $1.00 → 절당 $0.50이어야 한다(행당 $0.20이 아니라).
        for op, cost in [
            ("section_write:1.1", "0.30"),
            ("section_write:1.1", "0.20"),
            ("section_write:1.2", "0.20"),
            ("section_write:1.2", "0.20"),
            ("section_write:1.2", "0.10"),
        ]:
            test_session.add(_usage(proj.id, op, cost))
        # 절 작성이 아닌 사용량은 섞이면 안 된다.
        test_session.add(_usage(proj.id, "retrieval.hyde", "5.00"))
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{proj.id}/cost-basis", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["basis"] == "project"
        assert body["n_sections_measured"] == 2
        assert abs(body["per_section_usd"] - 0.50) < 0.001
        # 누적은 절 작성 밖까지 전부 — 예상치를 견줄 기준이다.
        assert abs(body["spent_usd"] - 6.00) < 0.001

    async def test_falls_back_to_same_model_tier_and_says_so(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """한 번도 안 쓴 보고서 — 같은 등급 다른 보고서 평균을 쓰되 근거를 밝힌다."""
        done = await _project(test_session, worker_user.id, model_mode="premium")
        test_session.add(_usage(done.id, "section_write:1.1", "0.80"))
        fresh = await _project(test_session, worker_user.id, model_mode="premium")
        await test_session.commit()

        body = (
            await test_client.get(
                f"/api/v1/projects/{fresh.id}/cost-basis", headers=_auth(worker_token)
            )
        ).json()
        assert body["basis"] == "model", "남의 평균을 자기 실측인 척하면 안 된다"
        assert abs(body["per_section_usd"] - 0.80) < 0.001

    async def test_no_samples_says_none_instead_of_guessing(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """표본이 없으면 None — 지어낸 숫자보다 "모른다"가 낫다."""
        proj = await _project(test_session, worker_user.id, model_mode="etc-없는등급")
        await test_session.commit()
        body = (
            await test_client.get(
                f"/api/v1/projects/{proj.id}/cost-basis", headers=_auth(worker_token)
            )
        ).json()
        assert body["per_section_usd"] is None
        assert body["basis"] == "none"


class TestSourceImpact:
    async def test_counts_sections_citations_and_sole_evidence(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """제외 전에 센다 — 절 수·인용 건수·**유일한 근거**인 절.

        유일한 근거가 가장 아프다: 빼면 그 절은 근거 0이 되어, 다시 쓰지 않으면 무근거
        서술만 남는다. 그 사실을 누른 뒤에 알면 되돌리는 값이 절당 $0.4~$1.3이다.
        """
        proj = await _project(test_session, worker_user.id)
        target = ProjectSource(
            project_id=proj.id, title="GTX 사업 효과 평가", source_type="upload", is_included=True
        )
        other = ProjectSource(
            project_id=proj.id, title="통계청 인구총조사", source_type="upload", is_included=True
        )
        test_session.add_all([target, other])
        await test_session.flush()

        c1, c2, c3 = (
            Chunk(
                project_id=proj.id, source_id=target.id, track="content", content="a", chunk_index=0
            ),
            Chunk(
                project_id=proj.id, source_id=target.id, track="content", content="b", chunk_index=1
            ),
            Chunk(
                project_id=proj.id, source_id=other.id, track="content", content="c", chunk_index=0
            ),
        )
        test_session.add_all([c1, c2, c3])
        await test_session.flush()

        # 1.1 = 이 자료만 인용(유일한 근거), 1.2 = 이 자료 + 다른 자료
        test_session.add_all(
            [
                Section(
                    id=uuid.uuid4(),
                    project_id=proj.id,
                    chapter_number=1,
                    section_number=1,
                    chapter_title="1장",
                    title="유일한 근거 절",
                    content="본문",
                    source_ids=[c1.id, c2.id],
                    status="completed",
                ),
                Section(
                    id=uuid.uuid4(),
                    project_id=proj.id,
                    chapter_number=1,
                    section_number=2,
                    chapter_title="1장",
                    title="다른 근거도 있는 절",
                    content="본문",
                    source_ids=[c1.id, c3.id],
                    status="completed",
                ),
                # 이 자료를 안 쓴 절은 목록에 없어야 한다.
                Section(
                    id=uuid.uuid4(),
                    project_id=proj.id,
                    chapter_number=1,
                    section_number=3,
                    chapter_title="1장",
                    title="무관한 절",
                    content="본문",
                    source_ids=[c3.id],
                    status="completed",
                ),
            ]
        )
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{proj.id}/sources/{target.id}/impact", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["n_sections"] == 2
        assert body["n_citations"] == 3  # 1.1이 2건, 1.2가 1건
        assert body["n_sole"] == 1
        labels = [s["label"] for s in body["sections"]]
        assert labels == ["1.1 유일한 근거 절", "1.2 다른 근거도 있는 절"]
        assert body["sections"][0]["sole"] is True
        assert body["sections"][1]["sole"] is False

    async def test_already_excluded_source_is_not_a_consolation(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """남은 근거는 **채택된** 자료만 센다.

        이미 빠진 자료가 남아 있다고 "다른 근거가 있다"고 하면, 실제로는 근거 0인 절을
        안전하다고 잘못 말하게 된다.
        """
        proj = await _project(test_session, worker_user.id)
        target = ProjectSource(
            project_id=proj.id, title="대상", source_type="upload", is_included=True
        )
        gone = ProjectSource(
            project_id=proj.id, title="이미 뺀 자료", source_type="upload", is_included=False
        )
        test_session.add_all([target, gone])
        await test_session.flush()
        c1 = Chunk(
            project_id=proj.id, source_id=target.id, track="content", content="a", chunk_index=0
        )
        c2 = Chunk(
            project_id=proj.id, source_id=gone.id, track="content", content="b", chunk_index=0
        )
        test_session.add_all([c1, c2])
        await test_session.flush()
        test_session.add(
            Section(
                id=uuid.uuid4(),
                project_id=proj.id,
                chapter_number=1,
                section_number=1,
                chapter_title="1장",
                title="절",
                content="본문",
                source_ids=[c1.id, c2.id],
                status="completed",
            )
        )
        await test_session.commit()

        body = (
            await test_client.get(
                f"/api/v1/projects/{proj.id}/sources/{target.id}/impact",
                headers=_auth(worker_token),
            )
        ).json()
        assert body["n_sole"] == 1, "이미 빠진 자료는 남은 근거로 쳐 주면 안 된다"

    async def test_unused_source_reports_nothing_so_the_dialog_stays_quiet(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """아무 절도 인용하지 않은 자료 — 화면은 확인창 없이 그냥 뺀다."""
        proj = await _project(test_session, worker_user.id)
        src = ProjectSource(
            project_id=proj.id, title="안 쓴 자료", source_type="upload", is_included=True
        )
        test_session.add(src)
        await test_session.commit()

        body = (
            await test_client.get(
                f"/api/v1/projects/{proj.id}/sources/{src.id}/impact", headers=_auth(worker_token)
            )
        ).json()
        assert body == {"n_sections": 0, "n_citations": 0, "n_sole": 0, "sections": []}
