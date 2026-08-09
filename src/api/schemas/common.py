from __future__ import annotations

from pydantic import BaseModel


class Page[ItemT](BaseModel):
    """페이지네이션 공통 응답 포맷."""

    total: int
    page: int
    page_size: int
    items: list[ItemT]
