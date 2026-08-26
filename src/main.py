import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.middleware.error_handler import register_error_handlers
from src.api.middleware.ip_whitelist import IPWhitelistMiddleware
from src.api.middleware.logging import LoggingMiddleware
from src.api.routers import api_v1_router
from src.api.routers.notify import router as notify_router
from src.api.routers.ws import router as ws_router
from src.core.config import settings
from src.core.logging import configure_logging
from src.db.session import async_engine

# ─────────────────────────────────────────────────────────────────────────────
# Sentry — 앱 내부 에러 모니터링.
#
# 위치가 중요하다: 이 블록은 반드시 `app = FastAPI(...)` **앞**에서 돌아야 한다.
# Starlette/FastAPI 통합이 ASGI 앱과 미들웨어 스택을 패치하는 방식이라, 앱이
# 만들어진 뒤에 init하면 요청 계측이 붙지 않는다. lifespan 안(:configure_logging
# 옆)도 같은 이유로 늦다 — 모듈 import 시점에 이미 app 객체가 완성된다.
#
# DSN이 비어 있으면 init 자체를 건너뛴다. 로컬 개발과 CI(SENTRY_DSN 미설정)는
# sentry-sdk가 설치돼 있어도 아무 동작을 하지 않는다.
# ─────────────────────────────────────────────────────────────────────────────
if settings.sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    # 스크러빙 deny-list — include_local_variables=False의 2차 방어선.
    # 근거: src/core/config.py의 시크릿 필드(jwt_secret_key, nw_private_key,
    # secrets_encryption_key, *_api_key, *_remote_token, postgres_password,
    # database_url)와 src/api/routers/auth.py의 SAML 처리 경로.
    _SENTRY_DENY_KEYS = (
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "private_key",
        "jwt",
        "saml",
        "dsn",
        "database_url",
        "x509",
    )

    # 이벤트에 실리는 문자열 상한. 보고서 본문·출처 발췌가 프레임에 얹히면
    # 기밀성과 이벤트 크기가 동시에 문제가 된다(src/workflows/write_loop.py 계열).
    _SENTRY_MAX_STR = 1024

    # 정상 흐름으로 처리되는 예외 — 이벤트로 올리지 않는다.
    # 401/403/404/422/429는 사용자 입력·권한의 결과이지 장애가 아니고,
    # 로그인 실패가 이벤트로 쏟아지면 Sentry quota를 그대로 태운다.
    # (src/infrastructure/auth/jwt_handler.py, password_handler.py,
    #  src/api/routers/ws.py, src/api/middleware/error_handler.py 참조)
    _SENTRY_IGNORED_EXCEPTIONS = frozenset(
        {
            "AuthenticationError",
            "AuthorizationError",
            "NotFoundError",
            "ValidationError",
            "RequestValidationError",
            "QuotaExceededError",
            "CostLimitExceededError",
            "RunCancelled",
            "WebSocketDisconnect",
        }
    )

    def _sentry_scrub(obj: Any, depth: int = 0) -> Any:
        """이벤트 페이로드에서 시크릿 키를 가리고 긴 문자열을 자른다."""
        if depth > 6:
            return "[truncated]"
        if isinstance(obj, dict):
            scrubbed: dict[Any, Any] = {}
            for key, value in obj.items():
                lowered = str(key).lower()
                if any(deny in lowered for deny in _SENTRY_DENY_KEYS):
                    scrubbed[key] = "[Filtered]"
                else:
                    scrubbed[key] = _sentry_scrub(value, depth + 1)
            return scrubbed
        if isinstance(obj, (list, tuple)):
            return [_sentry_scrub(item, depth + 1) for item in obj[:50]]
        if isinstance(obj, str) and len(obj) > _SENTRY_MAX_STR:
            return obj[:_SENTRY_MAX_STR] + "…[truncated]"
        return obj

    def _sentry_before_send(
        event: dict[str, Any], hint: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        exc = (hint or {}).get("exc_info", (None, None, None))[1]
        if exc is not None and type(exc).__name__ in _SENTRY_IGNORED_EXCEPTIONS:
            return None
        for key in ("request", "extra", "contexts", "user", "tags", "breadcrumbs"):
            if key in event:
                event[key] = _sentry_scrub(event[key])
        return event

    def _sentry_traces_sampler(sampling_context: dict[str, Any]) -> float:
        """헬스체크와 WebSocket은 추적하지 않는다.

        /health는 ALB·docker healthcheck가 계속 두드려 트랜잭션 quota만 먹고,
        /ws는 장수명 연결이라 트랜잭션 모델에 맞지 않는다.
        """
        path = str((sampling_context.get("asgi_scope") or {}).get("path", ""))
        if path == "/health" or path.startswith("/ws"):
            return 0.0
        return settings.sentry_traces_sample_rate

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        # 기존 Environment enum(local/staging/production)을 그대로 재사용한다.
        # .env의 ENVIRONMENT가 이미 배포 환경을 구분하고 있다.
        environment=settings.environment.value,
        # ── 민감정보 차단 ────────────────────────────────────────────────────
        # 쿠키의 access_token/refresh_token(api/routers/auth.py의 _set_auth_cookies),
        # 로그인 본문의 평문 비밀번호(api/schemas/auth.py), 클라이언트 IP를 막는다.
        send_default_pii=False,
        # saml_acs(api/routers/auth.py)는 하나의 try 블록 안에
        # raw_xml(평문 SAML assertion)·access_token·refresh_token을 지역변수로 들고
        # 있고, config의 nw_private_key_pem은 RSA 개인키를 프레임에 올린다.
        # send_default_pii=False로는 프레임 로컬이 막히지 않는다.
        include_local_variables=False,
        max_request_body_size="never",
        before_send=_sentry_before_send,
        # ── 성능 ─────────────────────────────────────────────────────────────
        traces_sample_rate=settings.sentry_traces_sample_rate,
        traces_sampler=_sentry_traces_sampler,
        profiles_sample_rate=0.0,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        # 스택 트레이스에서 우리 코드만 in-app으로 강조한다.
        in_app_include=["src"],
        attach_stacktrace=False,
        max_breadcrumbs=50,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # 관리자 설정(app_settings) 인메모리 캐시 로드 — .env 오버라이드를 반영.
    # 테이블이 아직 없거나(마이그레이션 전) DB 미가용이면 조용히 넘어간다(env만 사용).
    try:
        from src.core import app_settings

        await app_settings.refresh_cache()
    except Exception:
        pass
    yield
    await async_engine.dispose()


app = FastAPI(
    title="주식회사 로운인사이트 - AI 보고서 자동생성 시스템",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(LoggingMiddleware)
app.add_middleware(IPWhitelistMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(api_v1_router)
app.include_router(notify_router, prefix="/api/v1")
# 진행 상황 WebSocket — 앱 루트(/ws/...), same-origin. /api/v1 아님.
app.include_router(ws_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    # 환경 문자열은 노출하지 않는다(정보 최소화) — 로드밸런서/헬스체크엔 status면 충분.
    return {"status": "healthy"}
