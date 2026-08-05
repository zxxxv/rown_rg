from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.token_usage import TokenUsage
from src.db.models.user import User

pytestmark = pytest.mark.integration


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _add_usage(session: AsyncSession, user: User, cost_usd: Decimal) -> None:
    session.add(
        TokenUsage(
            user_id=user.id,
            model="claude-opus-4-7",
            operation="test_op",
            input_tokens=100,
            output_tokens=100,
            cost_usd=cost_usd,
            mode="replay",
        )
    )
    await session.commit()


class TestCostLimitRouteWiring:
    async def test_run_endpoint_returns_429_when_limit_exceeded(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        worker_user: User,
        worker_token: str,
    ) -> None:
        await _add_usage(test_session, worker_user, Decimal("200"))  # worker 기본 한도(200) 도달

        response = await test_client.post(
            f"/api/v1/projects/{uuid4()}/run", headers=_auth(worker_token)
        )

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "COST_LIMIT_EXCEEDED"

    async def test_run_endpoint_not_blocked_when_under_limit(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        response = await test_client.post(
            f"/api/v1/projects/{uuid4()}/run", headers=_auth(worker_token)
        )

        # 비용 게이트를 통과하면 다음 단계(존재하지 않는 프로젝트 조회)에서 404가 나야 한다.
        assert response.status_code == 404
