"""원격 GPU 리랭커 클라이언트 — 재채점만 GPU 박스로 내보낸다.

리랭킹은 절당 140초(후보 96개, 2026-08-20 3차 런 실측)로 리허설 시간의 대부분을
차지한다. 점수는 **저장되지 않고 순위 결정에만 쓰이므로** 임베딩과 달리 벡터 공간
일관성 문제가 없다 — 그래서 원격화 1순위다.

원격이 죽어도 보고서 생성은 계속돼야 한다. 사내 서비스가 개인 PC 전원에 매달리면
첫 정전이 사고가 된다. 폴백은 두 가지 중 설정으로 고른다:

- ``local``(기본): 서버 CPU 모델로 다시 채점. 품질 동일, 느림. **이 폴백을 쓰는 한
  서버는 CPU 모델의 메모리 피크를 감당할 크기를 유지해야 한다.**
- ``passthrough``: 재채점을 건너뛰고 검색 순위를 그대로 통과시킨다. 품질이 조금
  내려가는 대신 서버에서 모델을 완전히 들어낼 수 있다.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from src.clients.remote_stats import RemoteCallStats
from src.clients.reranker_client import RerankerClient
from src.core.clock import now
from src.core.config import settings

logger = structlog.get_logger(__name__)

FALLBACK_LOCAL = "local"
FALLBACK_PASSTHROUGH = "passthrough"

# passthrough 폴백이 만들어 내는 합성 점수의 시작값과 간격. 엄격히 내림차순이라
# rerank_hits의 정렬이 입력(검색) 순서를 그대로 보존하고, select_relevant의 비율
# 하한(1위 × 0.10)도 전원 통과한다 — 즉 "리랭킹만 없던 것으로" 동작한다.
_PASSTHROUGH_TOP = 1.0
_PASSTHROUGH_STEP = 0.001
# 간격을 유지하면 1000개째에서 0이 되므로 바닥을 둔다(하한 통과 보장).
_PASSTHROUGH_FLOOR = 0.2


class RemoteRerankerClient(RerankerClient):
    """HTTP로 GPU 추론 서비스에 재채점을 위임하고, 실패하면 폴백한다."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_s: float | None = None,
        connect_timeout_s: float | None = None,
        cooldown_s: float | None = None,
        fallback: str | None = None,
        client: httpx.AsyncClient | None = None,
        local_factory: Any = None,
    ) -> None:
        self._base_url = (base_url or settings.reranker_remote_url).rstrip("/")
        self._token = token if token is not None else settings.reranker_remote_token
        self._timeout_s = timeout_s or settings.reranker_remote_timeout_s
        self._connect_timeout_s = connect_timeout_s or settings.reranker_remote_connect_timeout_s
        self._cooldown_s = (
            cooldown_s if cooldown_s is not None else settings.reranker_remote_cooldown_s
        )
        self._fallback = fallback or settings.reranker_remote_fallback
        self._client = client
        # 로컬 폴백 모델은 **처음 실패할 때까지 만들지 않는다**. 원격을 쓰는 이유가
        # 서버에서 모델을 안 올리는 것인데 여기서 미리 만들면 그 이득이 사라진다.
        self._local: RerankerClient | None = None
        self._local_factory = local_factory
        # 연속 실패 시 매 절마다 타임아웃을 다시 기다리지 않도록 잠시 원격을 끈다.
        # 20절 × 60초 = 20분을 죽은 서비스를 기다리며 버리는 사고를 막는다.
        self._disabled_until: float = 0.0
        self.stats = RemoteCallStats()

        logger.info(
            "reranker.remote.configured",
            base_url=self._base_url,
            fallback=self._fallback,
            timeout_s=self._timeout_s,
            has_token=bool(self._token),
        )

    async def score_pairs(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []

        if self._in_cooldown():
            return await self._fallback_scores(query, passages, reason="cooldown")

        t0 = time.perf_counter()
        try:
            scores = await self._request_scores(query, passages)
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 폴백으로 살린다
            # 429(GPU 큐 포화)는 **쿨다운을 걸지 않는다**. 서비스가 죽은 게 아니라
            # 잠깐 밀린 것이라, 60초를 통째로 건너뛰면 순간적인 몰림 때문에 그 뒤의
            # 한가한 1분까지 CPU로 처리하게 된다. 429는 이미 즉시 돌아오므로
            # 매번 시도해도 비용이 거의 없다.
            busy = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            if not busy:
                self._disabled_until = time.monotonic() + self._cooldown_s
            self.stats.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning(
                "reranker.remote.failed",
                error=type(exc).__name__,
                detail=str(exc)[:200],
                n_passages=len(passages),
                cooldown_s=0 if busy else self._cooldown_s,
                busy=busy,
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            return await self._fallback_scores(query, passages, reason="error")

        self.stats.record_ok()
        logger.info(
            "reranker.remote.completed",
            n_passages=len(passages),
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return scores

    async def _request_scores(self, query: str, passages: list[str]) -> list[float]:
        client = self._ensure_client()
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        # 코루틴 차원 상한 - 터널이 끊은 소켓의 대기가 깨어나지 못한 채 영구
        # 정지한 실사례(2026-08-21 v6, 임베딩 경로) 대응. 임베딩·파서와 동일 벨트.
        response = await asyncio.wait_for(
            client.post(
                f"{self._base_url}/v1/rerank",
                json={"query": query, "passages": passages},
                headers=headers,
            ),
            timeout=self._timeout_s + 30,
        )
        response.raise_for_status()
        payload = response.json()
        return self._validate(payload, len(passages))

    @staticmethod
    def _validate(payload: Any, expected: int) -> list[float]:
        """응답을 믿기 전에 개수와 형을 확인한다.

        rerank_hits가 ``zip(hits, scores, strict=True)``로 묶기 때문에 개수가 하나만
        어긋나도 ValueError가 검색 한복판에서 터진다. 여기서 잡아서 폴백으로 보내는
        편이 낫다 — 원격 서비스 버전이 앞서 나가 응답 형이 바뀌는 상황이 실제로 온다.
        """
        if not isinstance(payload, dict):
            raise ValueError(f"응답이 객체가 아님: {type(payload).__name__}")
        scores = payload.get("scores")
        if not isinstance(scores, list):
            raise ValueError("응답에 scores 배열이 없음")
        if len(scores) != expected:
            raise ValueError(f"점수 개수 불일치: {len(scores)} != {expected}")
        out: list[float] = []
        for s in scores:
            if isinstance(s, bool) or not isinstance(s, (int, float)):
                raise ValueError(f"점수가 수가 아님: {s!r}")
            value = float(s)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"점수가 [0,1] 밖: {value}")
            out.append(value)
        return out

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s, connect=self._connect_timeout_s),
            )
        return self._client

    def _in_cooldown(self) -> bool:
        return time.monotonic() < self._disabled_until

    def stats_snapshot(self) -> dict[str, Any]:
        """모니터 라우터 노출용 — 폴백 누적과 쿨다운 여부."""
        return {
            "mode": "remote",
            "fallback_policy": self._fallback,
            "base_url": self._base_url,
            **self.stats.snapshot(disabled_until_monotonic=self._disabled_until),
        }

    async def _fallback_scores(
        self, query: str, passages: list[str], *, reason: str
    ) -> list[float]:
        self.stats.record_fallback(reason, items=len(passages))
        if self._fallback == FALLBACK_PASSTHROUGH:
            logger.warning(
                "reranker.remote.fallback.passthrough",
                reason=reason,
                n_passages=len(passages),
                at=now().isoformat(),
            )
            return self._passthrough_scores(len(passages))

        logger.warning(
            "reranker.remote.fallback.local",
            reason=reason,
            n_passages=len(passages),
            at=now().isoformat(),
        )
        return await self._local_client().score_pairs(query, passages)

    def _local_client(self) -> RerankerClient:
        if self._local is None:
            if self._local_factory is not None:
                self._local = self._local_factory()
            else:
                from src.clients.reranker_client import BgeRerankerV2M3Client

                self._local = BgeRerankerV2M3Client()
        return self._local

    @staticmethod
    def _passthrough_scores(count: int) -> list[float]:
        return [
            max(_PASSTHROUGH_FLOOR, _PASSTHROUGH_TOP - i * _PASSTHROUGH_STEP) for i in range(count)
        ]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
