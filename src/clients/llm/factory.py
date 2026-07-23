from collections.abc import Callable
from pathlib import Path

from src.clients.llm.adapters import AnthropicAdapter, GeminiAdapter, OpenAIAdapter
from src.clients.llm.base import LLMClient, LLMMode
from src.clients.llm.cassette import CassetteManager
from src.clients.llm.router import LLMRouter
from src.core import app_settings
from src.core.config import Environment, settings

DEFAULT_CASSETTE_DIR = Path("cassettes")


def determine_mode(environment: Environment) -> tuple[LLMMode, bool]:
    """
    환경에 따라 모드 설정
    PRODUCTION(live)    api 호출 O | 저장 X | 실제 사용
    STAGING(record)     api 호출 O | 저장 O | 개발(녹화)
    LOCAL(replay)       api 호출 X | 로드   | 개발(재생)
    """
    if environment == Environment.PRODUCTION:
        return "live", False
    if environment == Environment.STAGING:
        return "record", False
    return "replay", True


def create_llm_client(
    *,
    mode: LLMMode | None = None,
    allow_replay_fallback: bool | None = None,
    cassette_dir: Path | None = None,
) -> LLMClient:
    if mode is None:
        resolved_mode, fallback_default = determine_mode(settings.environment)
    else:
        resolved_mode = mode
        fallback_default = False
    fallback = allow_replay_fallback if allow_replay_fallback is not None else fallback_default

    cassettes = CassetteManager(cassette_dir or DEFAULT_CASSETTE_DIR)

    # provider별 어댑터 빌더 - 모델 ID로 LLMRouter가 골라 lazy 생성
    # API 키는 유효 설정(DB 오버라이드 → env)에서 읽는다. 어댑터는 lazy 생성이라
    # 빌더 실행 시점의 최신 키를 쓴다(설정 저장 후 reset_llm_client로 싱글턴 재생성).
    builders: dict[str, Callable[[], LLMClient]] = {
        "anthropic": lambda: AnthropicAdapter(
            api_key=app_settings.get_str("anthropic_api_key"),
            mode=resolved_mode,
            cassette_manager=cassettes,
            allow_replay_fallback=fallback,
        ),
        "gemini": lambda: GeminiAdapter(
            api_key=app_settings.get_str("gemini_api_key"),
            mode=resolved_mode,
            cassette_manager=cassettes,
            allow_replay_fallback=fallback,
        ),
        "openai": lambda: OpenAIAdapter(
            api_key=app_settings.get_str("openai_api_key"),
            mode=resolved_mode,
            cassette_manager=cassettes,
            allow_replay_fallback=fallback,
        ),
    }
    return LLMRouter(builders)


_singleton: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = create_llm_client()
    return _singleton


def reset_llm_client() -> None:
    global _singleton
    _singleton = None
