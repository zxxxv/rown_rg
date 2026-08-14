"""검증 커버리지 — PM 경고 화면이 "무엇을 검사했고 무엇을 못 보는가"를 함께 보이게.

경고 0건이 '깨끗함'으로 읽히는 순간 게이트가 안심 장치로 오작동한다(2026-08-14
지침). 진짜 결함 4건이 검사 밖 축(절 간 지표 대조·자료 시점·코퍼스 외 사실)에
있는데 critical 0이 뜬 실측이 근거다. 축별 설명 문구는 화면이 갖고, 여기는
분모 숫자만 준다.

값은 저장하지 않고 조회 시 계산한다 — 순수 함수(claim_coverage) 합산이라 싸고,
본문을 고치면 값도 따라온다. 별도 파일인 이유: projects.py는 진행 중 작업이
잦은 대형 모듈이라, 읽기 전용 집계 하나 때문에 같은 파일을 붙들지 않는다.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.core.config import settings
from src.db.models.section import Section
from src.db.models.user import User
from src.services.qa.gate import claim_coverage

router = APIRouter(prefix="/projects", tags=["projects"])


class VerifyCoverageRead(BaseModel):
    """검사 분모 요약 — PM 경고 카드의 '이 검사가 보는 범위' 표시용.

    claim_coverage: 주장 후보(제목·표·캡션 제외) 중 검사망에 들어간 문장 비율.
    missed_numeric: 수치를 실었는데 검사망 밖인 문장 수 — 구조 규칙상 0이어야
    하고, 0이 아니면 분해 회귀다(문장 분할·수치 정의·캡션 필터 중 하나가 깨짐).
    """

    n_sections: int
    n_candidates: int
    n_claims: int
    claim_coverage: float | None
    missed_numeric: int
    # 근거 동봉 판정(claim_verify) - 꺼져 있으면 '무근거 수치' critical이 뜰 수 없다
    llm_verify_enabled: bool
    # 챕터 횡단 LLM 검증(pm_verify) - 형식·법령 시점·수치 일관성 축의 존재 여부
    pm_verify_enabled: bool


@router.get("/{project_id}/verify-coverage", response_model=VerifyCoverageRead)
async def get_verify_coverage(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> VerifyCoverageRead:
    """검증 커버리지 요약 — 뷰어도 열람 가능(읽기 전용, verify-report와 동일 가드)."""
    # 순환 임포트 회피 - projects.py가 이 모듈을 모르게 하고, 권한 판정만 빌려 쓴다.
    from src.api.routers.projects import _get_authorized_project

    project = await _get_authorized_project(project_id, session, current_user)
    contents = (
        (
            await session.execute(
                select(Section.content).where(
                    Section.project_id == project.id, Section.content != ""
                )
            )
        )
        .scalars()
        .all()
    )
    picked = 0
    total = 0
    missed = 0
    for content in contents:
        p, t, m = claim_coverage(content or "")
        picked += p
        total += t
        missed += len(m)
    return VerifyCoverageRead(
        n_sections=len(contents),
        n_candidates=total,
        n_claims=picked,
        claim_coverage=round(picked / total, 3) if total else None,
        missed_numeric=missed,
        llm_verify_enabled=settings.claim_verify_enabled,
        pm_verify_enabled=settings.pm_verify_enabled,
    )
