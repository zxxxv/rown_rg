from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.api.dependencies.db import get_async_session
from src.core.config import settings
from src.db.base import Base
from src.db.models.user import User
from src.infrastructure.auth.password_handler import hash_password
from src.main import app

TEST_DB_NAME = "rown_test"


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter() -> None:
    """각 테스트 전에 로그인 실패 rate limiter의 인메모리 상태를 비운다(테스트 격리).

    프로세스 전역 dict라, 테스트들이 같은 client IP로 반복 로그인하면 누적돼 429가 난다.
    """
    from src.infrastructure.auth import rate_limiter

    rate_limiter.clear()


def _swap_db_name(url: str, dbname: str) -> str:
    base, _ = url.rsplit("/", 1)
    return f"{base}/{dbname}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _ensure_test_database() -> AsyncIterator[None]:
    """Create the rown_test database (drop if exists) for the test session."""
    admin_url = _swap_db_name(settings.database_url, "postgres")
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    await admin_engine.dispose()

    test_url = _swap_db_name(settings.database_url, TEST_DB_NAME)
    test_engine = create_async_engine(test_url)
    async with test_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgroonga"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    await test_engine.dispose()

    yield

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    await admin_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_db_engine(_ensure_test_database: None) -> AsyncIterator[AsyncEngine]:
    """Function-scoped test engine with fresh schema each test."""
    test_url = _swap_db_name(settings.database_url, TEST_DB_NAME)
    engine = create_async_engine(test_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_maker(
    test_db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def test_session(
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncSession]:
    """Yields a test session AND wires the app/middleware/token_tracker to use it."""

    async def _override_get_async_session() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_async_session] = _override_get_async_session
    monkeypatch.setattr("src.db.session.async_session_maker", test_session_maker)
    monkeypatch.setattr("src.api.middleware.ip_whitelist.async_session_maker", test_session_maker)
    monkeypatch.setattr("src.clients.llm.token_tracker.async_session_maker", test_session_maker)
    monkeypatch.setattr("src.api.routers.ws.async_session_maker", test_session_maker)

    try:
        async with test_session_maker() as session:
            yield session
    finally:
        app.dependency_overrides.pop(get_async_session, None)


@pytest_asyncio.fixture
async def test_client(test_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─────────────────────────────────────────────────────────────
# User fixtures (4 roles)
# ─────────────────────────────────────────────────────────────

FIXTURE_PASSWORD = "TestPassword123!@"


async def _make_user(
    session: AsyncSession, email: str, role: str, name: str, username: str | None = None
) -> User:
    user = User(
        email=email,
        username=username,
        name=name,
        role=role,
        password_hash=hash_password(FIXTURE_PASSWORD),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def super_admin_user(test_session: AsyncSession) -> User:
    return await _make_user(test_session, "sadmin@test.com", "super_admin", "Super Admin")


@pytest_asyncio.fixture
async def admin_user(test_session: AsyncSession) -> User:
    return await _make_user(test_session, "admin@test.com", "admin", "Admin")


@pytest_asyncio.fixture
async def worker_user(test_session: AsyncSession) -> User:
    return await _make_user(test_session, "worker@test.com", "worker", "Worker")


@pytest_asyncio.fixture
async def viewer_user(test_session: AsyncSession) -> User:
    return await _make_user(test_session, "viewer@test.com", "viewer", "Viewer")


async def _login(test_client: AsyncClient, email: str) -> str:
    resp = await test_client.post(
        "/api/v1/auth/login",
        json={"login_id": email, "password": FIXTURE_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


@pytest_asyncio.fixture
async def super_admin_token(test_client: AsyncClient, super_admin_user: User) -> str:
    return await _login(test_client, super_admin_user.email)


@pytest_asyncio.fixture
async def admin_token(test_client: AsyncClient, admin_user: User) -> str:
    return await _login(test_client, admin_user.email)


@pytest_asyncio.fixture
async def worker_token(test_client: AsyncClient, worker_user: User) -> str:
    return await _login(test_client, worker_user.email)


@pytest_asyncio.fixture
async def viewer_token(test_client: AsyncClient, viewer_user: User) -> str:
    return await _login(test_client, viewer_user.email)


# ─────────────────────────────────────────────────────────────
# LLM cassette tmp dir
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def cassette_tmp_dir(tmp_path: Path) -> Path:
    return tmp_path / "cassettes"


# Legacy: keep the old `app` fixture for any tests that referenced it.
@pytest.fixture
def fastapi_app():
    return app
