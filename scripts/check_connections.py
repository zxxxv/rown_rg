"""LLM provider 키 연동 확인 — .env/OS env의 키로 각 provider에 최소 ping.

provider별 최저가 모델로 max_tokens 소량 호출해 실제 연결을 검증한다. 키가 비었거나
플레이스홀더면 live 호출을 건너뛰고 '미설정'으로 표시한다(무의미한 401 회피). 키 값은
마스킹해서만 출력한다. OS 환경변수가 .env보다 우선하므로 둘 중 어디에 넣어도 잡힌다.

사용: `python -m scripts.check_connections`  (mode=live로 실호출 — 소량 토큰 과금)
"""

from __future__ import annotations

import asyncio

from src.clients.llm.base import CompletionRequest, Message
from src.clients.llm.factory import create_llm_client
from src.core.config import settings

# provider별 연결 테스트용 최저가 모델 (카탈로그 models.py 기준).
TEST_MODEL: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-2.5-flash-lite",
    "openai": "gpt-5.4-nano",
}

# 실제 키가 아니라고 보는 신호 (플레이스홀더/미설정).
_PLACEHOLDER_MARKERS = ("...", "change-me", "your_", "xxx")
_MIN_REAL_KEY_LEN = 20


def _key_for(provider: str) -> str:
    return {
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
        "openai": settings.openai_api_key,
    }[provider]


def _mask(key: str) -> str:
    if not key:
        return "(빈값)"
    if len(key) <= 10:
        return key[:2] + "***"
    return f"{key[:6]}…{key[-4:]}(len={len(key)})"


def _looks_placeholder(key: str) -> bool:
    if len(key) < _MIN_REAL_KEY_LEN:
        return True
    low = key.lower()
    return any(m in low for m in _PLACEHOLDER_MARKERS)


async def _check(provider: str) -> str:
    key = _key_for(provider)
    masked = _mask(key)
    if _looks_placeholder(key):
        return f"{provider:10s} key={masked:26s} → ⏭  미설정/플레이스홀더 (live 호출 건너뜀)"
    model = TEST_MODEL[provider]
    try:
        client = create_llm_client(mode="live")
        resp = await client.complete(
            CompletionRequest(
                model=model,
                messages=[Message(role="user", content="ping")],
                max_tokens=8,
                temperature=0.0,
            )
        )
        return (
            f"{provider:10s} key={masked:26s} → ✅ OK "
            f"({model}, in={resp.input_tokens}/out={resp.output_tokens})"
        )
    except Exception as e:  # noqa: BLE001 — 연결 실패 사유를 그대로 보고
        return f"{provider:10s} key={masked:26s} → ❌ 실패: {type(e).__name__}: {str(e)[:120]}"


async def main() -> None:
    print("=== LLM provider 연동 확인 (mode=live) ===")
    for provider in ("anthropic", "gemini", "openai"):
        print("  " + await _check(provider))
    print("\n키를 넣은 뒤 다시 실행하면 실연결이 검증됩니다 (.env 또는 OS 환경변수).")


if __name__ == "__main__":
    asyncio.run(main())
