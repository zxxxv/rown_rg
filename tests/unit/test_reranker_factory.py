"""리랭커 팩토리 분기 — 앱의 나머지는 이 한 관문만 본다."""

from __future__ import annotations

import pytest

from src.clients import reranker_factory
from src.clients.remote_reranker_client import RemoteRerankerClient


@pytest.fixture(autouse=True)
def _reset():
    reranker_factory.reset_reranker_client()
    yield
    reranker_factory.reset_reranker_client()


def test_remote_url_selects_remote_client(monkeypatch):
    monkeypatch.setattr(reranker_factory.settings, "reranker_remote_url", "http://gpu.local:8009")
    assert isinstance(reranker_factory.get_reranker_client(), RemoteRerankerClient)


def test_remote_client_is_singleton(monkeypatch):
    monkeypatch.setattr(reranker_factory.settings, "reranker_remote_url", "http://gpu.local:8009")
    assert reranker_factory.get_reranker_client() is reranker_factory.get_reranker_client()


def test_empty_url_does_not_touch_remote(monkeypatch):
    """URL이 비면 원격 경로를 아예 안 만든다 - 기본 배포는 지금과 똑같이 돈다."""
    monkeypatch.setattr(reranker_factory.settings, "reranker_remote_url", "")
    created: list[str] = []
    monkeypatch.setattr(
        reranker_factory,
        "BgeRerankerV2M3Client",
        lambda: created.append("local") or object(),
    )
    reranker_factory.get_reranker_client()
    assert created == ["local"]
