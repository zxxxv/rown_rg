"""GPU 추론 모니터 — 관리자 대시보드의 데이터 원천.

GPU 서비스는 집 PC라 관리자가 밖에서 직접 볼 수 없다(루프백 터널뿐이다).
이 라우터가 앱을 경유해 상태를 밖으로 낸다: 앱 → 터널 → GPU 서비스의
``/health``·``/stats/history``를 그대로 이어 주고, 여기에 **앱 쪽에서만 아는 것**
— 원격 클라이언트의 폴백 누적 — 을 합친다. GPU 서비스가 죽어 있어도 이 응답은
성공해야 한다: "죽어 있음"이야말로 대시보드가 보여줄 첫 번째 정보다.
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends

from src.api.dependencies.permissions import require_role
from src.clients.embedding_factory import peek_embedding_client
from src.clients.parser.remote import peek_remote_docling
from src.clients.reranker_factory import peek_reranker_client
from src.core.config import settings
from src.core.types import Role
from src.db.models.user import User

router = APIRouter(prefix="/admin/gpu", tags=["admin"])

# 상태 조회는 짧게 끊는다. 추론 타임아웃(60초)을 쓰면 GPU 박스가 죽었을 때
# 대시보드가 60초 동안 빙글빙글 돈다 - 터널이 살아 있으면 1초면 충분하다.
_PROBE_TIMEOUT_S = 5.0


def _client_stats(client: Any) -> dict[str, Any]:
    """원격 클라이언트면 폴백 통계, 로컬이면 mode만, 아직 안 만들어졌으면 unused."""
    if client is None:
        return {"mode": "unused"}
    snapshot = getattr(client, "stats_snapshot", None)
    if snapshot is None:
        return {"mode": "local"}
    return snapshot()


@router.get("")
async def gpu_monitor(
    # 최고관리자 전용 - 인프라(집 GPU 박스) 상태는 일반 관리자의 관심사가 아니고,
    # 폴백 에러 문자열에 내부 주소가 섞일 수 있다.
    _: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> dict[str, Any]:
    base_url = (
        settings.reranker_remote_url or settings.embedding_remote_url or settings.parser_remote_url
    ).rstrip("/")

    service: dict[str, Any]
    if not base_url:
        service = {"reachable": False, "configured": False, "error": "원격 GPU 미설정"}
    else:
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
                health_res = await client.get(f"{base_url}/health")
                health_res.raise_for_status()
                history_res = await client.get(f"{base_url}/stats/history")
                history_res.raise_for_status()
            service = {
                "reachable": True,
                "configured": True,
                "health": health_res.json(),
                "history": history_res.json(),
            }
        except Exception as exc:  # noqa: BLE001 — 도달 불가 자체가 보여줄 정보다
            service = {
                "reachable": False,
                "configured": True,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }

    return {
        "gpu_service": service,
        "clients": {
            "reranker": _client_stats(peek_reranker_client()),
            "embedding": _client_stats(peek_embedding_client()),
            "parser": _client_stats(peek_remote_docling()),
        },
    }
