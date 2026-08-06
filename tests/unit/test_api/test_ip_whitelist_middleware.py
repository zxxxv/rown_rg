"""IPWhitelistMiddleware 실제 차단 검증 — DB·서버 없이 dispatch 로직만 단위 검증.

확인 대상:
- LOCAL 환경은 무조건 통과(집행 안 함)
- 비-LOCAL(staging/production)에서 화이트리스트 미일치 IP는 403 차단
- 화이트리스트 일치 IP는 통과
- 빈 화이트리스트 = 전원 차단(fail-closed) — 운영 배포 시 락아웃 주의점
- /health 등 bypass 경로는 차단 환경에서도 통과
- X-Forwarded-For가 client.host보다 우선
"""

from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from src.api.middleware import ip_whitelist as mw_mod
from src.api.middleware.ip_whitelist import IPWhitelistMiddleware
from src.core.config import Environment, settings


class _FakeResult:
    def __init__(self, cidrs: list[str]) -> None:
        self._cidrs = cidrs

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[str]:
        return self._cidrs


class _FakeSession:
    def __init__(self, cidrs: list[str]) -> None:
        self._cidrs = cidrs

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._cidrs)


def _session_maker(cidrs: list[str]):
    def maker() -> _FakeSession:
        return _FakeSession(cidrs)

    return maker


def _request(
    path: str = "/api/v1/projects", client_ip: str = "203.0.113.5", xff: str | None = None
) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": headers,
        "client": (client_ip, 40000),
        "query_string": b"",
    }
    return Request(scope)


async def _pass(_request: Request) -> Response:
    return Response("ok", status_code=200)


def _mw() -> IPWhitelistMiddleware:
    return IPWhitelistMiddleware(
        app=lambda scope, receive, send: None
    )  # app 미사용(dispatch 직접 호출)


@pytest.fixture
def staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", Environment.STAGING)


class TestIpBlocking:
    async def test_local_always_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # LOCAL은 화이트리스트가 비어도, IP가 미일치여도 통과 — 집행 안 함.
        monkeypatch.setattr(settings, "environment", Environment.LOCAL)
        monkeypatch.setattr(mw_mod, "async_session_maker", _session_maker([]))
        resp = await _mw().dispatch(_request(client_ip="8.8.8.8"), _pass)
        assert resp.status_code == 200

    async def test_non_whitelisted_ip_blocked(self, staging: None, monkeypatch) -> None:
        monkeypatch.setattr(mw_mod, "async_session_maker", _session_maker(["10.0.0.0/8"]))
        resp = await _mw().dispatch(_request(client_ip="203.0.113.5"), _pass)
        assert resp.status_code == 403
        assert resp.body == b'{"error":{"code":"IP_FORBIDDEN","message":"ip not whitelisted"}}'

    async def test_whitelisted_ip_passes(self, staging: None, monkeypatch) -> None:
        monkeypatch.setattr(mw_mod, "async_session_maker", _session_maker(["203.0.113.0/24"]))
        resp = await _mw().dispatch(_request(client_ip="203.0.113.5"), _pass)
        assert resp.status_code == 200

    async def test_empty_whitelist_blocks_everyone(self, staging: None, monkeypatch) -> None:
        # ⚠️ 화이트리스트가 비면 전원 차단(fail-closed).
        # 운영 활성화 시 자기 IP를 먼저 넣어야 락아웃 안 됨.
        monkeypatch.setattr(mw_mod, "async_session_maker", _session_maker([]))
        resp = await _mw().dispatch(_request(client_ip="203.0.113.5"), _pass)
        assert resp.status_code == 403

    async def test_bypass_path_passes_when_blocked(self, staging: None, monkeypatch) -> None:
        # /health는 빈 화이트리스트(차단 환경)에서도 통과 — 헬스체크 보호.
        monkeypatch.setattr(mw_mod, "async_session_maker", _session_maker([]))
        resp = await _mw().dispatch(_request(path="/health", client_ip="8.8.8.8"), _pass)
        assert resp.status_code == 200

    async def test_forwarded_for_takes_precedence(self, staging: None, monkeypatch) -> None:
        # 프록시 뒤에서는 X-Forwarded-For의 첫 IP로 판정.
        monkeypatch.setattr(mw_mod, "async_session_maker", _session_maker(["203.0.113.0/24"]))
        # client.host는 미일치(10.x)지만 XFF가 일치(203.0.113.9) → 통과
        resp = await _mw().dispatch(
            _request(client_ip="10.1.2.3", xff="203.0.113.9, 70.1.1.1"), _pass
        )
        assert resp.status_code == 200
