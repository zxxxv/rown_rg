from typing import Any

import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BaseError,
    CostLimitExceededError,
    DatabaseError,
    LLMError,
    NotFoundError,
    QuotaExceededError,
    ValidationError,
)

logger = structlog.get_logger(__name__)


def _response(
    status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": body})


# pydantic 요청 검증 실패(422)를 한국어 이유로 바꾼다. 원문(msg)은 영어라 그대로 내면
# 어느 칸이 왜 틀렸는지 알 수 없다 — 신규 에이전트 저장이 "입력을 확인해주세요"로만
# 실패하던 건(2026-08-12 QA). 커스텀 검증(ValueError)은 이미 한국어라 접두사만 걷는다.
def _friendly_reason(err: dict[str, Any]) -> str:
    ctx = err.get("ctx") or {}
    kind = err.get("type", "")
    if kind == "missing":
        return "필수 항목입니다"
    if kind == "string_too_short":
        min_length = ctx.get("min_length")
        return "값을 입력해주세요" if min_length == 1 else f"최소 {min_length}자 이상이어야 합니다"
    if kind == "string_too_long":
        return f"최대 {ctx.get('max_length')}자까지 입력할 수 있습니다"
    if kind == "greater_than_equal":
        return f"{ctx.get('ge')} 이상이어야 합니다"
    if kind == "less_than_equal":
        return f"{ctx.get('le')} 이하여야 합니다"
    if kind == "too_long":
        return f"최대 {ctx.get('max_length')}개까지 지정할 수 있습니다"
    if kind in ("int_parsing", "int_type", "float_parsing", "decimal_parsing"):
        return "숫자여야 합니다"
    if kind in ("enum", "literal_error"):
        return "허용되지 않는 값입니다"
    if kind == "json_invalid":
        return "요청 본문이 올바른 JSON이 아닙니다"
    msg = str(err.get("msg", ""))
    return msg.removeprefix("Value error, ")


def _field_path(loc: tuple[Any, ...]) -> str:
    """('body','spec','min_chars') → 'spec.min_chars'. 어느 필드인지가 핵심 정보다."""
    return ".".join(str(x) for x in loc if x not in ("body", "query", "path", "header"))


def _code(exc: BaseError, default: str) -> str:
    return exc.code or default


def _capture_server_error(exc: Exception, error_type: str) -> None:
    # 5xx로 나가는 예외를 Sentry에 명시적으로 올린다.
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("error_type", error_type)
        sentry_sdk.capture_exception(exc)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationError)
    async def _auth(_: Request, exc: AuthenticationError) -> JSONResponse:
        return _response(401, _code(exc, "UNAUTHENTICATED"), exc.message)

    @app.exception_handler(AuthorizationError)
    async def _authz(_: Request, exc: AuthorizationError) -> JSONResponse:
        return _response(403, _code(exc, "FORBIDDEN"), exc.message)

    @app.exception_handler(NotFoundError)
    async def _notfound(_: Request, exc: NotFoundError) -> JSONResponse:
        return _response(404, _code(exc, "NOT_FOUND"), exc.message)

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, exc: ValidationError) -> JSONResponse:
        return _response(422, _code(exc, "VALIDATION_ERROR"), exc.message)

    # FastAPI 본문 검증 실패는 우리 봉투를 안 거치고 {detail:[...]}로 새던 구멍 —
    # 프론트 공통 클라이언트가 봉투만 읽어 정체불명 문구가 됐다. 같은 봉투로 통일한다.
    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": _field_path(tuple(err.get("loc", ()))), "message": _friendly_reason(err)}
            for err in exc.errors()
        ]
        message = " · ".join(
            f"{f['field']}: {f['message']}" if f["field"] else f["message"] for f in fields
        )
        logger.warning("request.validation", path=request.url.path, fields=fields)
        return _response(422, "VALIDATION_ERROR", message, details={"fields": fields})

    @app.exception_handler(LLMError)
    async def _llm(_: Request, exc: LLMError) -> JSONResponse:
        logger.error("llm.error", code=exc.code, message=exc.message)
        _capture_server_error(exc, "llm_gateway")
        return _response(502, _code(exc, "LLM_ERROR"), exc.message)

    @app.exception_handler(QuotaExceededError)
    async def _quota(_: Request, exc: QuotaExceededError) -> JSONResponse:
        logger.warning("quota.exceeded", code=exc.code, message=exc.message)
        return _response(429, _code(exc, "QUOTA_EXCEEDED"), exc.message)

    # cost_limit 게이트는 옵션 B에서 엔드포인트에 배선하지 않지만(집행은 quota_gate 단일),
    # 예외/핸들러는 무해하게 유지 — 향후 특정 엔드포인트 사전 차단용으로 재사용 가능.
    @app.exception_handler(CostLimitExceededError)
    async def _cost_limit(_: Request, exc: CostLimitExceededError) -> JSONResponse:
        logger.warning("cost_limit.blocked", code=exc.code, message=exc.message)
        return _response(429, _code(exc, "COST_LIMIT_EXCEEDED"), exc.message)

    @app.exception_handler(DatabaseError)
    async def _db(_: Request, exc: DatabaseError) -> JSONResponse:
        logger.error("db.error", code=exc.code, message=exc.message)
        _capture_server_error(exc, "database")
        return _response(500, _code(exc, "DATABASE_ERROR"), exc.message)

    @app.exception_handler(BaseError)
    async def _base(_: Request, exc: BaseError) -> JSONResponse:
        logger.error("app.error", code=exc.code, message=exc.message)
        _capture_server_error(exc, "application")
        return _response(500, _code(exc, "INTERNAL_ERROR"), exc.message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled.error", path=request.url.path)
        return _response(500, "INTERNAL_ERROR", "internal server error")
