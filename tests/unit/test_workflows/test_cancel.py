"""취소 레지스트리 단위 테스트 — request/clear/raise 신호."""

from __future__ import annotations

import uuid

import pytest

from src.workflows import cancel


def test_registry_signal_lifecycle() -> None:
    pid = uuid.uuid4()
    assert not cancel.is_requested(pid)
    cancel.raise_if_cancelled(pid)  # 요청 없음 → no-op

    cancel.request(pid)
    assert cancel.is_requested(pid)
    with pytest.raises(cancel.RunCancelled):
        cancel.raise_if_cancelled(pid)

    cancel.clear(pid)
    assert not cancel.is_requested(pid)
    cancel.raise_if_cancelled(pid)  # 정리 후 → no-op

    # 격리: 다른 프로젝트 id는 영향 없음
    other = uuid.uuid4()
    cancel.request(pid)
    cancel.raise_if_cancelled(other)
    cancel.clear(pid)
