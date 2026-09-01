"""관리자 설정(app_settings) — .env 값을 UI에서 채운다(super_admin 전용).

시크릿은 암호화 저장 + 응답에선 평문 비노출(설정됨/미설정만). 저장 즉시 인메모리 캐시에
반영(apply_override)하고, LLM 키 변경은 팩토리 싱글턴을 리셋해 다음 호출부터 새 키를 쓴다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import require_role
from src.api.schemas.settings import (
    SettingItem,
    SettingOption,
    SettingsResponse,
    SettingUpdate,
)
from src.core import app_settings
from src.core.app_settings import DEF_BY_KEY, SETTING_DEFS, SettingDef
from src.core.exceptions import NotFoundError, ValidationError
from src.core.secrets import encrypt
from src.core.types import Role
from src.db.models.app_setting import AppSetting
from src.db.models.user import User

router = APIRouter(prefix="/admin/settings", tags=["admin"])


def _item(d: SettingDef) -> SettingItem:
    overridden = app_settings.is_overridden(d.key)
    configured = app_settings.is_configured(d.key)
    source = "db" if overridden else ("env" if configured else "none")
    if d.secret:
        value = None
    elif d.kind == "bool":
        # 파이썬 bool의 str()은 "True"라 화면에 그대로 노출됐다 — 계약을 소문자로 고정.
        value = "true" if app_settings.get_bool(d.key) else "false"
    else:
        value = app_settings.get_str(d.key)
    options = (
        [SettingOption(value=v, label=lbl) for v, lbl in d.options] if d.kind == "enum" else None
    )
    return SettingItem(
        key=d.key,
        label=d.label,
        group=d.group,
        kind=d.kind,
        is_secret=d.secret,
        configured=configured,
        source=source,
        value=value,
        options=options,
    )


@router.get("", response_model=SettingsResponse)
async def list_settings(
    _: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> SettingsResponse:
    return SettingsResponse(items=[_item(d) for d in SETTING_DEFS])


@router.put("/{key}", response_model=SettingItem)
async def set_setting(
    key: str,
    data: SettingUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> SettingItem:
    d = DEF_BY_KEY.get(key)
    if d is None:
        raise NotFoundError(message=f"알 수 없는 설정 키: {key}", code="UNKNOWN_SETTING")

    value = data.value.strip()
    # enum은 카탈로그 밖의 값을 막는다(오타·미지원 모델 방지).
    if d.kind == "enum" and value != "" and value not in {v for v, _ in d.options}:
        raise ValidationError(message=f"허용되지 않은 값: {value}", code="INVALID_SETTING_VALUE")
    row = await session.get(AppSetting, key)

    if value == "":
        # 오버라이드 해제 → env로 복귀
        if row is not None:
            await session.delete(row)
        app_settings.apply_override(key, None)
    else:
        stored = encrypt(value) if d.secret else value
        if row is None:
            session.add(
                AppSetting(key=key, value=stored, is_secret=d.secret, updated_by=current_user.id)
            )
        else:
            row.value = stored
            row.is_secret = d.secret
            row.updated_by = current_user.id
        app_settings.apply_override(key, value)

    await session.flush()
    # LLM 키가 바뀌면 팩토리 싱글턴을 리셋해 다음 호출부터 새 키로 어댑터를 만든다.
    if key in ("anthropic_api_key", "gemini_api_key", "openai_api_key"):
        from src.clients.llm.factory import reset_llm_client

        reset_llm_client()
    return _item(d)
