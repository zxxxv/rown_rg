import asyncio
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

import structlog

from src.clients.llm import quota_gate, token_tracker
from src.clients.llm.base import CompletionRequest, CompletionResponse, LLMMode
from src.clients.llm.cassette import (
    CassetteManager,
    compute_input_hash,
    resolve_cache_key,
)
from src.clients.llm.cost import CostCalculator
from src.clients.llm.exceptions import CassetteNotFoundError, LLMAPIError, LLMRateLimitError

logger = structlog.get_logger(__name__)

# 재시도 분류: rate_limit·retryable은 백오프 재시도, fatal은 즉시 실패
RetryKind = str  # "rate_limit" | "retryable" | "fatal"


class BaseLLMAdapter(ABC):
    """
    provider 공통 오케스트레이션(캐셋 record/replay · 재시도 · 비용/토큰 추적)을 담는 베이스.
    provider별 차이는 서브클래스가 _create_client / _call_provider / _classify_error 로만 구현한다.
    """

    provider: str = "unknown"  # 서브클래스에서 지정 (로깅용)

    def __init__(
        self,
        *,
        api_key: str,
        mode: LLMMode,
        cassette_manager: CassetteManager,
        max_retries: int = 3,
        base_retry_delay: float = 1.0,
        allow_replay_fallback: bool = False,
    ):
        self._api_key = api_key
        self.mode = mode
        self.cassette_manager = cassette_manager
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay
        self.allow_replay_fallback = allow_replay_fallback
        self._client: Any | None = self._create_client(api_key) if mode != "replay" else None

    @abstractmethod
    def _create_client(self, api_key: str) -> Any:
        """
        provider SDK 클라이언트 생성
        """

    @abstractmethod
    async def _call_provider(self, request: CompletionRequest) -> CompletionResponse:
        """
        실제 API 호출 + 응답을 CompletionResponse로 변환
        """

    @abstractmethod
    def _classify_error(self, exc: Exception) -> RetryKind | None:
        """
        예외를 'rate_limit' | 'retryable' | 'fatal'로 분류. provider 예외가 아니면 None
        """

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        operation = token_tracker.get_operation() or "unknown"
        input_hash = compute_input_hash(request)
        cache_key = resolve_cache_key(request, input_hash)

        effective_mode: LLMMode = self.mode
        if effective_mode == "replay" and self.allow_replay_fallback:
            if not self.cassette_manager.exists(operation, cache_key, input_hash):
                logger.info(
                    "llm.cassette.miss",
                    provider=self.provider,
                    operation=operation,
                    cache_key=cache_key,
                    fallback="record",
                )
                effective_mode = "record"
                if self._client is None:
                    self._client = self._ensure_client_for_fallback()

        if effective_mode == "replay":
            response = self.cassette_manager.load(operation, cache_key, input_hash)
        else:
            # 실호출 경로에서만 한도 검사 — replay는 실비용이 없어 통과.
            await quota_gate.enforce()
            response = await self._call_with_retry(request)
            if effective_mode == "record":
                await self.cassette_manager.save(
                    operation, cache_key, input_hash, request, response
                )

        cost = self._safe_cost(response)
        asyncio.create_task(
            token_tracker.record_usage_safe(
                model=response.model,
                # Anthropic usage의 input_tokens는 캐시 쓰기/읽기 분을 제외한 잔여분이다.
                # 캐시에 쓴 토큰도 실제로 처리(과금)된 입력이므로 원장에는 합산해 기록
                # — 별도 컬럼 없이 토큰 총량 대시보드가 진실을 유지한다. 정확한 단가
                # 분리는 cost_usd가 담당(_safe_cost가 1.25배 프리미엄 반영).
                input_tokens=response.input_tokens + response.cache_write_input_tokens,
                output_tokens=response.output_tokens,
                cached_input_tokens=response.cached_input_tokens,
                cost_usd=cost,
                mode=effective_mode,
            )
        )
        return response

    def _ensure_client_for_fallback(self) -> Any:
        if not self._api_key:
            raise CassetteNotFoundError(
                f"Replay fallback requires an API client for provider '{self.provider}'. "
                "Configure the provider api_key, or pre-record the cassette.",
            )
        return self._create_client(self._api_key)

    def _safe_cost(self, response: CompletionResponse) -> Decimal:
        # 가격표에 없는 모델이어도 LLM 호출 자체는 성공해야 하므로 best-effort.
        try:
            return CostCalculator.calculate(
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cached_input_tokens=response.cached_input_tokens,
                cache_write_input_tokens=response.cache_write_input_tokens,
            )
        except Exception:
            logger.warning(
                "llm.cost.unpriced_model",
                provider=self.provider,
                model=response.model,
            )
            return Decimal(0)

    async def _call_with_retry(self, request: CompletionRequest) -> CompletionResponse:
        delay = self.base_retry_delay
        last_err: Exception | None = None
        last_kind: RetryKind | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._call_provider(request)
            except Exception as exc:
                kind = self._classify_error(exc)
                if kind is None:
                    raise  # provider 예외가 아니면 그대로 전파
                if kind == "fatal":
                    raise LLMAPIError(f"{self.provider} API error: {exc}") from exc
                last_err = exc
                last_kind = kind
                logger.warning(
                    "llm.retry",
                    provider=self.provider,
                    kind=kind,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    delay_sec=delay,
                    model=request.model,
                    error_type=type(exc).__name__,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(delay)
                delay *= 2

        if last_kind == "rate_limit":
            raise LLMRateLimitError(str(last_err)) from last_err
        raise LLMAPIError(str(last_err)) from last_err
