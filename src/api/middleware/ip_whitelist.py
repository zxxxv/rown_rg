import ipaddress
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy import func, or_, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.core.config import Environment, settings
from src.db.models.ip_whitelist import IpWhitelist
from src.db.session import open_session

logger = structlog.get_logger(__name__)

BYPASS_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if settings.environment == Environment.LOCAL:
            return await call_next(request)

        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in BYPASS_PREFIXES):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        if not client_ip:
            return self._forbidden("client ip not resolvable")

        try:
            ip_obj = ipaddress.ip_address(client_ip)
        except ValueError:
            return self._forbidden("invalid client ip")

        async with open_session() as session:
            stmt = select(IpWhitelist.ip_cidr).where(
                IpWhitelist.is_active.is_(True),
                or_(IpWhitelist.expires_at.is_(None), IpWhitelist.expires_at > func.now()),
            )
            result = await session.execute(stmt)
            cidrs = result.scalars().all()

        for cidr in cidrs:
            try:
                network = ipaddress.ip_network(str(cidr), strict=False)
            except ValueError:
                continue
            if ip_obj in network:
                return await call_next(request)

        logger.warning("ip.blocked", client_ip=client_ip, path=path)
        return self._forbidden("ip not whitelisted")

    @staticmethod
    def _get_client_ip(request: Request) -> str | None:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return None

    @staticmethod
    def _forbidden(message: str) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "IP_FORBIDDEN", "message": message}},
        )
