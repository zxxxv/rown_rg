"""서술 구성 통계 — 접기 정확성과 완성 시 선계산 영속의 회귀 고정(2026-08-28).

조회 시점 계산은 절당 1~2초 × 절 수라 첫 클릭이 수십 초 걸렸다("미리 연산시켜서
띄울 수 없나"). 계약:
  ① 접기는 근거 패널의 claimTone과 같은 칸(confirmed/unconfirmed/uncited/defect)
  ② 계산 결과는 config[_evidence_composition]에 지문과 함께 영속된다
  ③ 지문이 같으면(본문 불변) 저장분을 읽고 판정 파이프라인을 다시 돌지 않는다
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routers import projects as router
from src.api.schemas.section import ClaimAlignmentRead, SectionEvidenceResponse
from src.db.models.project import Project
from src.db.models.section import Section


def _claim(status: str, span: str | None = None, ungrounded: list[str] | None = None):
    return ClaimAlignmentRead(
        claim="문장", status=status, span_text=span, ungrounded=ungrounded or []
    )


def _stub_payload() -> SectionEvidenceResponse:
    return SectionEvidenceResponse(
        section_id="x",
        claims=[
            _claim("aligned", span="대목"),  # confirmed
            _claim("aligned"),  # span 없음 -> unconfirmed
            _claim("weak"),  # unconfirmed
            _claim("uncited"),  # AI 서술
            _claim("aligned", span="대목", ungrounded=["42"]),  # defect가 우선
        ],
        uncovered=["명사 종결 항목"],
    )


async def _completed_project(session: AsyncSession, owner_id) -> Project:
    project = Project(
        title="서술 구성",
        topic="철강",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="completed",
    )
    session.add(project)
    await session.flush()
    session.add(
        Section(
            id=uuid4(),
            project_id=project.id,
            chapter_number=1,
            section_number=1,
            chapter_title="개요",
            title="1.1",
            content="본문 " * 30,
        )
    )
    await session.commit()
    return project


class TestEvidenceComposition:
    async def test_fold_and_persist(
        self, super_admin_user, test_session: AsyncSession, monkeypatch
    ) -> None:
        project = await _completed_project(test_session, super_admin_user.id)
        calls = {"n": 0}

        async def _stub(session, proj, row):
            calls["n"] += 1
            return _stub_payload()

        monkeypatch.setattr(router, "_section_evidence_payload", _stub)
        router._EVIDENCE_COMP_CACHE.clear()

        result = await router._compute_evidence_composition(test_session, project)
        assert (
            result.total.confirmed,
            result.total.unconfirmed,
            result.total.uncited,
            result.total.defect,
            result.total.uncovered,
        ) == (1, 2, 1, 1, 1)
        assert result.total.claims == 5
        (ch,) = result.chapters
        assert (ch.chapter_number, ch.title, ch.confirmed) == (1, "개요", 1)
        # ② 영속 - 재시작한 프로세스도 계산 없이 읽는다.
        stored = (project.config or {}).get("_evidence_composition")
        assert stored and stored.get("fingerprint")

        # ③ 지문 동일 + 메모리 캐시 냉각 -> 저장분으로 답하고 파이프라인은 안 돈다.
        router._EVIDENCE_COMP_CACHE.clear()
        again = await router._compute_evidence_composition(test_session, project)
        assert calls["n"] == 1
        assert again.total == result.total

    async def test_config_form_keeps_precomputed_key(self) -> None:
        # 폼의 옵션 전체 교체가 선계산분을 지우면 첫 조회가 도로 수십 초가 된다.
        assert "_evidence_composition" in router._INTERNAL_CONFIG_KEYS
