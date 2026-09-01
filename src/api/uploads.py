"""업로드 파일 공통 검증 — 확장자 허용목록 + 크기 상한(본문을 메모리로 읽기 전 거부).

라이브러리·프로젝트 자료 업로드가 같은 규칙을 쓰도록 한 곳에 모은다. 허용 확장자는
파서 레지스트리(SUPPORTED_EXTENSIONS)에서 파생하므로, 처리 불가능한 형식은 애초에 막힌다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from src.clients.parser.registry import SUPPORTED_EXTENSIONS
from src.core.exceptions import ValidationError


async def read_validated_upload(file: UploadFile, *, max_bytes: int) -> tuple[str, bytes]:
    """확장자·크기를 검증하고 본문을 읽어 ``(safe_name, content)``로 돌려준다.

    - 확장자가 파서 지원 목록에 없으면 ``UNSUPPORTED_FILE_TYPE``로 거부.
    - 크기는 본문을 읽기 전에 ``file.size``로 먼저 거부해 큰 파일을 메모리로 끌어오지 않는다.
      ``size``가 없으면(Content-Length 부재) 읽은 뒤 백스톱으로 재확인한다.
    """
    safe_name = Path(file.filename or "untitled").name
    ext = Path(safe_name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValidationError(
            message=f"지원하지 않는 파일 형식입니다({ext or '확장자 없음'}). 허용: {allowed}",
            code="UNSUPPORTED_FILE_TYPE",
        )

    limit_mb = max_bytes // (1024 * 1024)
    declared = getattr(file, "size", None)
    if declared is not None and declared > max_bytes:
        raise ValidationError(
            message=f"파일이 너무 큽니다(최대 {limit_mb}MB).",
            code="FILE_TOO_LARGE",
        )

    content = await file.read()
    if len(content) > max_bytes:  # size가 None이었던 경우 백스톱
        raise ValidationError(
            message=f"파일이 너무 큽니다(최대 {limit_mb}MB).",
            code="FILE_TOO_LARGE",
        )
    # 빈 파일은 여기서 막는다. 프로젝트 자료 업로드에만 검사가 있고 라이브러리 업로드에는
    # 없어서, 라이브러리로 올린 0바이트 파일이 자료로 등록됐다(2026-08-12 실측: 업로드 6건
    # 중 2건이 0 B·쪽수 미상). 파싱은 실패하는데 목록에는 정상 자료처럼 보여 사람이
    # "자료를 넣었다"고 믿는다. 같은 검증이 두 경로에 흩어져 있으면 또 어긋난다.
    if not content:
        raise ValidationError(message="빈 파일입니다.", code="EMPTY_UPLOAD")
    return safe_name, content
