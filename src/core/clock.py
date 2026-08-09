from datetime import UTC, datetime


def now() -> datetime:
    """
    재 시각을 UTC(timezone-aware)로 반환
    저장·계산은 항상 UTC, KST 변환은 표시 계층에서
    """
    return datetime.now(UTC)
