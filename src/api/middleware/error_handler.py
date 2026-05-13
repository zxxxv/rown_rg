import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BaseError,
    DatabaseError,
    LLMError,
    NotFoundError,
    ValidationError,
)

logger = structlog.get_logger(__name__)


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _code(exc: BaseError, default: str) -> str:
    return exc.code or default


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

    @app.exception_handler(LLMError)
    async def _llm(_: Request, exc: LLMError) -> JSONResponse:
        logger.error("llm.error", code=exc.code, message=exc.message)
        return _response(502, _code(exc, "LLM_ERROR"), exc.message)

    @app.exception_handler(DatabaseError)
    async def _db(_: Request, exc: DatabaseError) -> JSONResponse:
        logger.error("db.error", code=exc.code, message=exc.message)
        return _response(500, _code(exc, "DATABASE_ERROR"), exc.message)

    @app.exception_handler(BaseError)
    async def _base(_: Request, exc: BaseError) -> JSONResponse:
        logger.error("app.error", code=exc.code, message=exc.message)
        return _response(500, _code(exc, "INTERNAL_ERROR"), exc.message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled.error", path=request.url.path)
        return _response(500, "INTERNAL_ERROR", "internal server error")
