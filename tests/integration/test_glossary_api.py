"""정본 용어집 API — 확정(승격)·덮어쓰기·목록·후보·권한의 실 DB 관통."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.user import User
from tests.conftest import auth_headers as _auth

pytestmark = pytest.mark.asyncio


async def _seed_project_with_conflict(session: AsyncSession, owner: User) -> Project:
    """표기가 1:1로 갈린 자료 두 건을 가진 프로젝트 — 후보 파생의 최소 재료."""
    project = Project(id=uuid.uuid4(), title="용어 테스트", topic="RE100 대응", owner_id=owner.id)
    session.add(project)
    await session.flush()
    for title, ko in (("A 보고서", "알이백"), ("무역협회 자료", "재생에너지 사용 확인")):
        session.add(
            ProjectSource(
                project_id=project.id,
                source_type="upload",
                title=title,
                is_included=True,
                metadata_={
                    "term_entries": [
                        {
                            "ko": ko,
                            "en": "Renewable Electricity 100",
                            "abbr": "RE100",
                            "definition": None,
                            "context": f"{title}의 발견 문장",
                            "origin": "pattern",
                        }
                    ]
                },
            )
        )
    await session.commit()
    return project


async def test_confirm_is_upsert_within_scope(test_client: AsyncClient, worker_token: str) -> None:
    first = await test_client.post(
        "/api/v1/glossary",
        json={"en": "Equinix", "ko": "이퀴닉스", "source": "document"},
        headers=_auth(worker_token),
    )
    assert first.status_code == 200, first.text
    assert first.json()["project_id"] is None
    # 같은 층·같은 키 재확정 = 정정(행이 늘지 않는다)
    second = await test_client.post(
        "/api/v1/glossary",
        json={"en": "equinix", "ko": "에퀴닉스", "source": "convention"},
        headers=_auth(worker_token),
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["ko"] == "에퀴닉스"

    listed = await test_client.get("/api/v1/glossary", headers=_auth(worker_token))
    rows = [r for r in listed.json() if r["term_key"] == "equinix"]
    assert len(rows) == 1


async def test_project_override_layers_over_org(
    test_client: AsyncClient,
    test_session: AsyncSession,
    worker_user: User,
    worker_token: str,
) -> None:
    project = Project(id=uuid.uuid4(), title="덮어쓰기", topic="주제", owner_id=worker_user.id)
    test_session.add(project)
    await test_session.commit()

    org = await test_client.post(
        "/api/v1/glossary",
        json={"en": "green premium", "ko": "녹색프리미엄", "source": "document"},
        headers=_auth(worker_token),
    )
    assert org.status_code == 200
    override = await test_client.post(
        "/api/v1/glossary",
        json={
            "project_id": str(project.id),
            "en": "green premium",
            "ko": "그린 프리미엄",
            "source": "manual",
        },
        headers=_auth(worker_token),
    )
    assert override.status_code == 200
    assert override.json()["project_id"] == str(project.id)

    listed = await test_client.get(
        f"/api/v1/glossary?project_id={project.id}", headers=_auth(worker_token)
    )
    rows = [r for r in listed.json() if r["term_key"] == "green premium"]
    assert {r["ko"] for r in rows} == {"녹색프리미엄", "그린 프리미엄"}


async def test_candidates_derive_conflicts_and_confirmation_clears_them(
    test_client: AsyncClient,
    test_session: AsyncSession,
    worker_user: User,
    worker_token: str,
) -> None:
    project = await _seed_project_with_conflict(test_session, worker_user)

    got = await test_client.get(
        f"/api/v1/glossary/candidates?project_id={project.id}", headers=_auth(worker_token)
    )
    assert got.status_code == 200, got.text
    cands = got.json()
    assert len(cands) == 1
    assert cands[0]["term_key"] == "renewable electricity 100"
    assert {v["ko"] for v in cands[0]["variants"]} == {"알이백", "재생에너지 사용 확인"}
    assert all(v["context"] for v in cands[0]["variants"])  # 발견 문맥 동봉

    confirm = await test_client.post(
        "/api/v1/glossary",
        json={
            "project_id": str(project.id),
            "en": "Renewable Electricity 100",
            "abbr": "RE100",
            "ko": "알이백",
            "source": "document",
        },
        headers=_auth(worker_token),
    )
    assert confirm.status_code == 200
    again = await test_client.get(
        f"/api/v1/glossary/candidates?project_id={project.id}", headers=_auth(worker_token)
    )
    assert again.json() == []  # 확정된 키는 더는 후보가 아니다


async def test_viewer_cannot_confirm_or_delete(
    test_client: AsyncClient, viewer_token: str, worker_token: str
) -> None:
    denied = await test_client.post(
        "/api/v1/glossary",
        json={"en": "Deutsche Bahn", "ko": "독일철도", "source": "manual"},
        headers=_auth(viewer_token),
    )
    assert denied.status_code == 403

    made = await test_client.post(
        "/api/v1/glossary",
        json={"en": "Deutsche Bahn", "ko": "독일철도", "source": "manual"},
        headers=_auth(worker_token),
    )
    del_denied = await test_client.delete(
        f"/api/v1/glossary/{made.json()['id']}", headers=_auth(viewer_token)
    )
    assert del_denied.status_code == 403
    deleted = await test_client.delete(
        f"/api/v1/glossary/{made.json()['id']}", headers=_auth(worker_token)
    )
    assert deleted.status_code == 204
