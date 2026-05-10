from __future__ import annotations

import ipaddress
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IpWhitelistBase(BaseModel):
    ip_cidr: str = Field(..., description="IP CIDR 표기 (예: 192.168.1.0/24)")
    description: str | None = Field(None, max_length=255, description="설명")

    @field_validator("ip_cidr")
    @classmethod
    def validate_cidr(cls, v: str) -> str:
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"유효하지 않은 CIDR 표기: {v}") from e
        return v


class IpWhitelistCreate(IpWhitelistBase):
    is_active: bool = Field(True, description="활성 여부")
    expires_at: datetime | None = Field(None, description="만료 시간 (NULL이면 영구)")


class IpWhitelistUpdate(BaseModel):
    ip_cidr: str | None = None
    description: str | None = None
    is_active: bool | None = None
    expires_at: datetime | None = None

    @field_validator("ip_cidr")
    @classmethod
    def validate_cidr(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ipaddress.ip_network(v, strict=False)
            except ValueError as e:
                raise ValueError(f"유효하지 않은 CIDR 표기: {v}") from e
        return v


class IpWhitelistRead(IpWhitelistBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool
    expires_at: datetime | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
