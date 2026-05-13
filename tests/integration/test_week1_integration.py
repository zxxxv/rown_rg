from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.clients.base import CompletionRequest, CompletionResponse, Message
from src.clients.cassette_manager import (
    CassetteManager,
    compute_input_hash,
    resolve_cache_key,
)
from src.clients.exceptions import CassetteNotFoundError
from src.clients.factory import create_llm_client
from src.clients.token_tracker import token_context
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from tests.conftest import FIXTURE_PASSWORD


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _drain_background_tasks() -> None:
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ═══════════════════════════════════════════
class TestEnvironmentSetup:
    """환경·DB 셋업 검증."""

    async def test_health_endpoint(self, test_client: AsyncClient) -> None:
        response = await test_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"

    async def test_database_extensions(self, test_session: AsyncSession) -> None:
        result = await test_session.execute(
            text(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('vector', 'pgroonga', 'pgcrypto')"
            )
        )
        extensions = {row[0] for row in result}
        assert "vector" in extensions
        assert "pgroonga" in extensions
        assert "pgcrypto" in extensions

    async def test_all_tables_created(self, test_session: AsyncSession) -> None:
        expected = {
            "users",
            "ip_whitelist",
            "library_nodes",
            "projects",
            "project_sources",
            "chunks",
            "raptor_nodes",
            "consistency_graph_nodes",
            "token_usage",
        }
        result = await test_session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = {row[0] for row in result}
        missing = expected - tables
        assert not missing, f"missing tables: {missing}"


# ═══════════════════════════════════════════
class TestAuthentication:
    """인증 시스템 검증."""

    async def test_register_requires_admin(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        response = await test_client.post(
            "/api/v1/auth/register",
            headers=_auth(worker_token),
            json={
                "email": "new@test.com",
                "password": "ValidPass123!@",
                "name": "New",
                "role": "worker",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_register_by_admin(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        response = await test_client.post(
            "/api/v1/auth/register",
            headers=_auth(super_admin_token),
            json={
                "email": "new_worker@test.com",
                "password": "ValidPass123!@",
                "name": "New Worker",
                "role": "worker",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "new_worker@test.com"
        assert body["role"] == "worker"

    async def test_login_success(self, test_client: AsyncClient, super_admin_user: User) -> None:
        response = await test_client.post(
            "/api/v1/auth/login",
            json={"email": super_admin_user.email, "password": FIXTURE_PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["user"]["role"] == "super_admin"

    async def test_login_wrong_password(
        self, test_client: AsyncClient, super_admin_user: User
    ) -> None:
        response = await test_client.post(
            "/api/v1/auth/login",
            json={"email": super_admin_user.email, "password": "WrongPass123!@"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"

    async def test_login_failure_lockout(self, test_client: AsyncClient, worker_user: User) -> None:
        for _ in range(5):
            r = await test_client.post(
                "/api/v1/auth/login",
                json={"email": worker_user.email, "password": "WrongPass123!@"},
            )
            assert r.status_code == 401
            assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"

        locked = await test_client.post(
            "/api/v1/auth/login",
            json={"email": worker_user.email, "password": "WrongPass123!@"},
        )
        assert locked.status_code == 401
        assert locked.json()["error"]["code"] == "ACCOUNT_LOCKED"

    async def test_password_policy_violation(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        response = await test_client.post(
            "/api/v1/auth/register",
            headers=_auth(super_admin_token),
            json={
                "email": "bad@test.com",
                "password": "short",
                "name": "Bad",
                "role": "worker",
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PASSWORD_TOO_SHORT"

    async def test_me_returns_current_user(
        self, test_client: AsyncClient, admin_token: str, admin_user: User
    ) -> None:
        response = await test_client.get("/api/v1/auth/me", headers=_auth(admin_token))
        assert response.status_code == 200
        assert response.json()["email"] == admin_user.email

    async def test_me_unauthenticated(self, test_client: AsyncClient) -> None:
        response = await test_client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_refresh_token_works(
        self, test_client: AsyncClient, super_admin_user: User
    ) -> None:
        login = await test_client.post(
            "/api/v1/auth/login",
            json={"email": super_admin_user.email, "password": FIXTURE_PASSWORD},
        )
        refresh = login.json()["refresh_token"]
        r = await test_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 200
        assert r.json()["access_token"]


# ═══════════════════════════════════════════
class TestPermissions:
    """4-Role 권한 매트릭스 검증 (현재 라우터 범위: users 관리)."""

    async def test_viewer_cannot_list_users(
        self, test_client: AsyncClient, viewer_token: str
    ) -> None:
        response = await test_client.get("/api/v1/users", headers=_auth(viewer_token))
        assert response.status_code == 403

    async def test_worker_cannot_list_users(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        response = await test_client.get("/api/v1/users", headers=_auth(worker_token))
        assert response.status_code == 403

    async def test_admin_can_list_users(
        self, test_client: AsyncClient, admin_token: str, admin_user: User
    ) -> None:
        response = await test_client.get("/api/v1/users", headers=_auth(admin_token))
        assert response.status_code == 200
        emails = [u["email"] for u in response.json()]
        assert admin_user.email in emails

    async def test_only_super_admin_can_delete(
        self,
        test_client: AsyncClient,
        admin_token: str,
        super_admin_token: str,
        worker_user: User,
    ) -> None:
        admin_resp = await test_client.delete(
            f"/api/v1/users/{worker_user.id}", headers=_auth(admin_token)
        )
        assert admin_resp.status_code == 403

        sa_resp = await test_client.delete(
            f"/api/v1/users/{worker_user.id}", headers=_auth(super_admin_token)
        )
        assert sa_resp.status_code == 204

    async def test_user_can_get_own_profile(
        self, test_client: AsyncClient, worker_token: str, worker_user: User
    ) -> None:
        response = await test_client.get(
            f"/api/v1/users/{worker_user.id}", headers=_auth(worker_token)
        )
        assert response.status_code == 200
        assert response.json()["email"] == worker_user.email

    async def test_user_cannot_get_other_profile(
        self,
        test_client: AsyncClient,
        worker_token: str,
        viewer_user: User,
    ) -> None:
        response = await test_client.get(
            f"/api/v1/users/{viewer_user.id}", headers=_auth(worker_token)
        )
        assert response.status_code == 403


# ═══════════════════════════════════════════
class TestLLMClient:
    """LLMClient 추상화 검증."""

    async def test_replay_mode_uses_cassette(
        self, cassette_tmp_dir: Path, test_session: AsyncSession
    ) -> None:
        cm = CassetteManager(cassette_tmp_dir)
        request = CompletionRequest(
            messages=[Message(role="user", content="hello cassette")],
            model="claude-opus-4-7",
            max_tokens=10,
            temperature=0.0,
        )
        expected = CompletionResponse(
            content="hi from cassette",
            input_tokens=5,
            output_tokens=4,
            cached_input_tokens=0,
            model="claude-opus-4-7",
            stop_reason="end_turn",
        )
        input_hash = compute_input_hash(request)
        cache_key = resolve_cache_key(request, input_hash)
        await cm.save("replay_op", cache_key, input_hash, request, expected)

        client = create_llm_client(
            mode="replay", allow_replay_fallback=False, cassette_dir=cassette_tmp_dir
        )
        async with token_context(operation="replay_op"):
            result = await client.complete(request)

        await _drain_background_tasks()
        assert result.content == "hi from cassette"
        assert result.input_tokens == 5
        assert result.output_tokens == 4

    async def test_replay_mode_missing_cassette_raises(
        self, cassette_tmp_dir: Path, test_session: AsyncSession
    ) -> None:
        client = create_llm_client(
            mode="replay", allow_replay_fallback=False, cassette_dir=cassette_tmp_dir
        )
        request = CompletionRequest(
            messages=[Message(role="user", content="no cassette here")],
            model="claude-opus-4-7",
            max_tokens=10,
        )
        with pytest.raises(CassetteNotFoundError):
            async with token_context(operation="missing_op"):
                await client.complete(request)

    async def test_token_usage_recorded(
        self, cassette_tmp_dir: Path, test_session: AsyncSession
    ) -> None:
        cm = CassetteManager(cassette_tmp_dir)
        request = CompletionRequest(
            messages=[Message(role="user", content="track me")],
            model="claude-opus-4-7",
            max_tokens=20,
            temperature=0.0,
        )
        response = CompletionResponse(
            content="tracked",
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=0,
            model="claude-opus-4-7",
            stop_reason="end_turn",
        )
        input_hash = compute_input_hash(request)
        cache_key = resolve_cache_key(request, input_hash)
        await cm.save("track_op", cache_key, input_hash, request, response)

        client = create_llm_client(
            mode="replay", allow_replay_fallback=False, cassette_dir=cassette_tmp_dir
        )
        async with token_context(operation="track_op"):
            await client.complete(request)

        await _drain_background_tasks()

        rows = (
            (
                await test_session.execute(
                    select(TokenUsage).where(TokenUsage.operation == "track_op")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.input_tokens == 10
        assert row.output_tokens == 5
        assert row.mode == "replay"
