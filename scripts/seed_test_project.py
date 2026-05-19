"""Create the Week 2 fixed-UUID test project.

Idempotent — if a project with the fixed UUID already exists, prints and
exits 0. Requires at least one ``super_admin`` user; uses the first one
returned by id ordering as the owner.

Usage:
    uv run python scripts/seed_test_project.py
"""

from __future__ import annotations

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select

from src.db.models.project import Project
from src.db.models.user import User
from src.db.session import async_engine, async_session_maker

FIXTURE_PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
FIXTURE_TITLE = "Week 2 검증용 테스트 프로젝트"
FIXTURE_TOPIC = "Week 2 인덱싱·검색 파이프라인 검증"


async def main() -> int:
    try:
        async with async_session_maker() as session:
            existing = await session.execute(
                select(Project).where(Project.id == FIXTURE_PROJECT_ID)
            )
            if existing.scalar_one_or_none() is not None:
                print(f"fixture project already exists: {FIXTURE_PROJECT_ID}")
                return 0

            owner_q = await session.execute(
                select(User).where(User.role == "super_admin").order_by(User.id).limit(1)
            )
            owner = owner_q.scalar_one_or_none()
            if owner is None:
                print(
                    "ERROR: no super_admin user found. Run scripts/create_initial_admin.py first.",
                    file=sys.stderr,
                )
                return 1

            session.add(
                Project(
                    id=FIXTURE_PROJECT_ID,
                    title=FIXTURE_TITLE,
                    topic=FIXTURE_TOPIC,
                    owner_id=owner.id,
                )
            )
            await session.commit()
            print(f"fixture project created: {FIXTURE_PROJECT_ID} (owner={owner.email})")
            return 0
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
