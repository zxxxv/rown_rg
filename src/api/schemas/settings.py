from __future__ import annotations

from pydantic import BaseModel, Field


class SettingOption(BaseModel):
    value: str
    label: str


class SettingItem(BaseModel):
    key: str
    label: str
    group: str
    kind: str  # str | number | bool | enum
    is_secret: bool
    configured: bool  # 값이 설정돼 있는지(설정됨/미설정)
    source: str  # "db"(관리자 입력) | "env" | "none"
    # 시크릿은 절대 평문을 돌려주지 않는다(항상 None). 비밀 아닌 값만 노출.
    value: str | None = None
    # kind="enum"일 때만 채워진다(드롭다운 선택지). 그 외엔 None.
    options: list[SettingOption] | None = None


class SettingsResponse(BaseModel):
    items: list[SettingItem]


class SettingUpdate(BaseModel):
    # 빈 문자열이면 오버라이드 해제(=env로 복귀). 시크릿은 저장 시 암호화된다.
    value: str = Field(..., max_length=8000)
