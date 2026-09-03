"""통합·유닛 테스트 공용 빌더 — 테스트 파일 간 교차 import를 끊는다(2026-09-04 정리).

배경: test_drift_api.py가 사실상 공유 픽스처 모듈 노릇을 해서 5개 파일이 그 안의
헬퍼를 직접 import했다 — 테스트 파일 하나를 리네임하면 5파일이 연쇄로 깨지는 구조.
잡 폴링 루프·소유자+프로젝트 시드도 같은 본문이 3벌씩 살았다.

원칙: 본문을 일반화하지 않고 그대로 옮긴다. flush/commit·상태값 차이는 의미 있는
차이(커밋 안 된 행은 앱 쪽 세션에서 안 보인다)라 호출부가 소유한다.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.types import SectionPlan
from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.user import User
from src.infrastructure.auth.password_handler import hash_password
from src.services.sections.drift import content_fingerprint


def drift_outline(direction: str, sid: uuid.UUID) -> dict:
    """1장 1절짜리 목차 — 미반영(드리프트) 계열 테스트의 공용 골격."""
    return {
        "chapters": [
            {
                "id": "ch1",
                "title": "1장",
                "sections": [
                    {
                        "id": str(sid),
                        "title": "배경",
                        "direction": direction,
                        "key_points": [],
                        "agents": [],
                    }
                ],
            }
        ]
    }


async def completed_project(
    session: AsyncSession, owner_id: uuid.UUID, sid: uuid.UUID, direction: str
) -> uuid.UUID:
    """완료 상태 프로젝트 + 본문·plan_hash가 있는 절 1개 — 커밋까지 한다."""
    plan = SectionPlan(
        section_id=sid,
        chapter_number=1,
        section_number=1,
        title="배경",
        chapter_title="1장",
        direction=direction,
    )
    proj = Project(
        title="미반영 테스트",
        topic="주제",
        config={
            "outline": drift_outline(direction, sid),
            "_section_plan": [plan.model_dump(mode="json")],
        },
        status="completed",
        depth_mode="full_report",
        owner_id=owner_id,
    )
    session.add(proj)
    await session.flush()
    session.add(
        Section(
            id=sid,
            project_id=proj.id,
            chapter_number=1,
            section_number=1,
            chapter_title="1장",
            title="배경",
            content="이미 쓰인 본문입니다.",
            source_ids=[],
            plan_hash=content_fingerprint(plan),
            status="completed",
        )
    )
    await session.commit()
    return proj.id


async def wait_job_done(pid: uuid.UUID, job_name: str, *, tries: int = 100) -> Any:
    """백그라운드 잡이 끝날 때까지 폴링 — **지우지는 않는다**.

    마지막으로 본 잡 상태(없으면 None)를 돌려준다. 사후 동작(failures assert·
    clear_job·보존)은 호출부 계약이다 — variants 계열은 "다 돈 잡을 지우지 않는 것"
    자체가 화면 계약이라, 여기서 clear하면 의미가 바뀐다.
    """
    from src.services.jobs import get_job

    job = None
    for _ in range(tries):
        job = get_job(pid, job_name)
        if job and not job.running:
            return job
        await asyncio.sleep(0.1)
    return job


async def seed_owner_project(session: AsyncSession, *, prefix: str) -> Project:
    """작성자 1명 + 그 소유 프로젝트 1건 — 커밋·refresh까지(검색 계열 테스트 시드)."""
    user = User(
        email=f"{prefix}-{uuid4().hex[:6]}@test.com",
        name=prefix,
        role="worker",
        password_hash=hash_password(f"{prefix.capitalize()}12345678!@"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    project = Project(title=f"{prefix}-project", topic="topic", owner_id=user.id)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project
