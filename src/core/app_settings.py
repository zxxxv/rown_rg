"""관리자 편집 가능한 운영 설정·시크릿 — DB(app_settings)가 .env를 오버라이드.

원칙:
- key는 Settings(config.py)의 속성명과 동일 → DB에 없으면 env/기본값으로 폴백.
- 시크릿은 DB에 암호문으로 저장하고, 응답에선 평문을 절대 노출하지 않는다(설정됨/미설정만).
- 소비 지점(LLM 팩토리·네이버웍스 클라이언트 등)은 settings.X 대신 여기 get_*를 쓴다.
- 인메모리 캐시(_cache)는 앱 시작·설정 저장 시 refresh_cache()로 갱신 — 소비자는 동기 read.

⚠️ 단일 워커 전제(러너·요청·설정저장이 같은 프로세스라 캐시 공유). 멀티워커면 각 워커가
   자기 시작 시점 캐시를 들고 있어 저장 직후 반영이 안 될 수 있다(그땐 재시작 or 외부 캐시).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from src.core.config import settings
from src.core.secrets import decrypt


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    secret: bool
    kind: str  # "str" | "number" | "bool" | "enum"
    group: str
    help: str = ""
    # kind="enum"일 때만 사용 — (값, 표시라벨) 쌍. 프론트가 드롭다운으로 렌더한다.
    options: tuple[tuple[str, str], ...] = ()


SETTING_DEFS: list[SettingDef] = [
    # LLM — API 키만 admin에서 관리. 모델 선택은 시스템 설정이 아니라 프로젝트 생성 단계의
    # 관심사라 여기서 노출하지 않는다(.env가 기본값 공급, 파이프라인은 그대로 소비).
    SettingDef("anthropic_api_key", "Anthropic API 키", True, "str", "LLM"),
    SettingDef("gemini_api_key", "Gemini API 키", True, "str", "LLM"),
    SettingDef("openai_api_key", "OpenAI API 키", True, "str", "LLM"),
    # NAVER WORKS
    SettingDef("nw_client_id", "네이버웍스 Client ID", False, "str", "네이버웍스"),
    SettingDef("nw_client_secret", "네이버웍스 Client Secret", True, "str", "네이버웍스"),
    SettingDef("nw_service_account", "네이버웍스 Service Account", False, "str", "네이버웍스"),
    SettingDef("nw_private_key", "네이버웍스 Private Key", True, "str", "네이버웍스"),
    SettingDef("nw_bot_id", "네이버웍스 Bot ID", False, "str", "네이버웍스"),
    # 운영
    SettingDef("org_monthly_cost_limit_usd", "조직 월 비용 상한(USD)", False, "number", "운영"),
    SettingDef("notify_enabled", "네이버웍스 알림 사용", False, "bool", "운영"),
]

DEF_BY_KEY: dict[str, SettingDef] = {d.key: d for d in SETTING_DEFS}

# 오버라이드된 값(시크릿은 복호화된 평문)만 담는다. 없으면 env 폴백.
_cache: dict[str, str] = {}


async def refresh_cache() -> None:
    """app_settings 전체를 읽어 인메모리 캐시를 재구성한다(시작·저장 시 호출)."""
    from src.db.models.app_setting import AppSetting
    from src.db.session import async_session_maker

    fresh: dict[str, str] = {}
    async with async_session_maker() as session:
        rows = (await session.execute(select(AppSetting))).scalars().all()
        for r in rows:
            if r.value is None:
                continue
            fresh[r.key] = decrypt(r.value) if r.is_secret else r.value
    _cache.clear()
    _cache.update(fresh)


def _env_str(key: str) -> str:
    v = getattr(settings, key, None)
    return "" if v is None else str(v)


def get_str(key: str) -> str:
    """유효 문자열 값(DB 오버라이드 우선, 없으면 env)."""
    return _cache[key] if key in _cache else _env_str(key)


def get_bool(key: str) -> bool:
    raw = _cache.get(key)
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(getattr(settings, key, False))


def get_decimal(key: str) -> Decimal:
    raw = _cache.get(key)
    if raw is not None:
        return Decimal(raw)
    return Decimal(str(getattr(settings, key)))


def is_configured(key: str) -> bool:
    """DB에 값이 있거나 env에 비어있지 않은 값이 있으면 True."""
    return key in _cache or _env_str(key) != ""


def is_overridden(key: str) -> bool:
    """DB 오버라이드가 있으면 True(env가 아니라 DB 값이 유효)."""
    return key in _cache


def apply_override(key: str, value: str | None) -> None:
    """저장 직후 인메모리 캐시를 즉시 반영한다(트랜잭션 커밋과 별개).

    value가 None/빈문자면 오버라이드 해제(=env 폴백). 시크릿도 여기엔 평문으로 담긴다
    (프로세스 메모리 한정). refresh_cache의 DB 재조회 없이 바로 최신 값이 유효해진다.
    """
    if not value:
        _cache.pop(key, None)
    else:
        _cache[key] = value
