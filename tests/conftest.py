from __future__ import annotations

import os
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

# 테스트 DB 이름은 **프로세스마다 다르다**. 예전엔 "rown_test" 하나를 공유해서 두 세션이
# 동시에 pytest를 돌리면 뒤에 시작한 쪽의 DROP DATABASE ... WITH (FORCE)가 앞 세션의 DB를
# 통째로 날렸다. 결과는 느려지는 정도가 아니라 **없는 실패를 만들어 내는 것**이었다
# (2026-08-25~26 실측: 유닛 스위트가 4분 → 17~30분, 매번 다른 테스트가 무작위로 실패).
# 이름을 갈라 두면 동시 실행이 서로를 밟지 않는다. CI가 이름을 고정하고 싶으면 env로 준다.
TEST_DB_NAME = os.environ.get("ROWN_TEST_DB") or f"rown_test_{os.getpid()}"
_STALE_PREFIX = "rown_test_"


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


async def _drop_orphan_test_databases(conn) -> None:
    """죽은 pytest 프로세스가 남긴 rown_test_* DB만 걷어낸다.

    프로세스별 이름을 쓰면 강제 종료된 런의 DB가 쌓인다. **살아 있는 프로세스의 것은
    절대 건드리지 않는다** — 이름 뒤의 pid가 지금 살아 있는지로 판정한다(연결 수로
    판정하면 테스트 사이에 연결이 잠깐 0인 세션의 DB를 날린다).
    """
    import psutil

    rows = (
        await conn.execute(
            text("SELECT datname FROM pg_database WHERE datname LIKE :p"),
            {"p": f"{_STALE_PREFIX}%"},
        )
    ).all()
    for (name,) in rows:
        if name == TEST_DB_NAME:
            continue
        suffix = name[len(_STALE_PREFIX) :]
        if not suffix.isdigit() or psutil.pid_exists(int(suffix)):
            continue
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _ensure_test_database() -> AsyncIterator[None]:
    """이 프로세스 전용 테스트 DB를 만든다(있으면 지우고 새로)."""
    admin_url = _swap_db_name(settings.database_url, "postgres")
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await _drop_orphan_test_databases(conn)
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
