from pathlib import Path

from src.clients.anthropic_adapter import AnthropicAdapter
from src.clients.base import LLMClient, LLMMode
from src.clients.cassette_manager import CassetteManager
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
    return AnthropicAdapter(
        api_key=settings.anthropic_api_key,
        mode=resolved_mode,
        cassette_manager=cassettes,
        allow_replay_fallback=fallback,
    )


_singleton: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = create_llm_client()
    return _singleton


def reset_llm_client() -> None:
    global _singleton
    _singleton = None
