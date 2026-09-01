"""업로드 검증 — 확장자·크기·빈 파일."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_빈_파일은_거부한다() -> None:
    """실측(2026-08-12): 업로드 6건 중 2건이 0 B로 등록됐다. 프로젝트 자료 업로드에만
    검사가 있고 라이브러리 업로드에는 없어서, 목록에는 정상 자료처럼 보이는데 파싱은
    실패했다. 검증을 공용 진입점으로 올려 두 경로가 같이 막히게 한다."""
    from src.api.uploads import read_validated_upload
    from src.core.exceptions import ValidationError

    class _Empty:
        filename = "빈파일.pdf"
        size = 0
        content_type = "application/pdf"

        async def read(self) -> bytes:
            return b""

    with pytest.raises(ValidationError) as exc:
        await read_validated_upload(_Empty(), max_bytes=10 * 1024 * 1024)
    assert exc.value.code == "EMPTY_UPLOAD"
