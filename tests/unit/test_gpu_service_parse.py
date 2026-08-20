"""GPU 파싱 엔드포인트 HTTP 계약 — 인증·게이트·크기 상한·레인 포화·데드라인.

모델 없이 서비스 대역으로 검증한다. 핵심 계약: (1) 문서 본문이 오가므로 인증은
fail-closed, (2) Content-Length를 믿지 않고 실제 바이트로 413, (3) placeholder는
받은 그대로 변환기에 전달(페이지 마커 계약은 앱 소유), (4) 임시 파일은 항상 정리.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpu_service.app import main as gpu_main
from gpu_service.app.gpu_queue import GpuBusy
from gpu_service.app.parse_service import ParseTimeout

AUTH = {"Authorization": "Bearer testtoken"}


class _FakeParseService:
    def __init__(self) -> None:
        self.ready = True
        self.device = "cuda"
        self.docling_version = "2.105.0"
        self.calls: list[dict] = []
        self.raise_exc: Exception | None = None
        self.seen_tmp_path: Path | None = None
        self.tmp_existed_during_parse: bool | None = None

    def load(self) -> None:  # lifespan이 부른다 - 대역은 로드할 것이 없다
        return None

    async def parse(self, tmp_path: Path, placeholder: str, is_disconnected):
        self.seen_tmp_path = tmp_path
        self.tmp_existed_during_parse = tmp_path.exists()
        self.calls.append({"placeholder": placeholder, "bytes": tmp_path.stat().st_size})
        if self.raise_exc is not None:
            raise self.raise_exc
        return {
            "markdown": "# Parsed",
            "page_count": 2,
            "image_count": 0,
            "elapsed_ms": 5.0,
            "device": self.device,
            "docling_version": self.docling_version,
        }


@pytest.fixture
def fake_service(monkeypatch) -> _FakeParseService:
    monkeypatch.setattr(
        gpu_main, "config", dataclasses.replace(gpu_main.config, token="testtoken")
    )
    monkeypatch.setattr(gpu_main.service, "load", lambda: None)
    fake = _FakeParseService()
    monkeypatch.setattr(gpu_main, "parse_service", fake)
    return fake


@pytest.fixture
def client(fake_service):
    with TestClient(gpu_main.app) as c:
        yield c


def _upload(client: TestClient, content: bytes = b"%PDF-1.4 x", **kwargs):
    return client.post(
        "/v1/parse",
        files={"file": ("doc.pdf", content, "application/pdf")},
        data={"page_break_placeholder": "<!-- rown:page-break -->"},
        headers=AUTH,
        **kwargs,
    )


class TestParseEndpoint:
    def test_success_passes_placeholder_verbatim(self, client, fake_service):
        response = _upload(client)
        assert response.status_code == 200
        body = response.json()
        assert body["markdown"] == "# Parsed"
        assert body["page_count"] == 2
        # 마커 계약은 앱 소유 - 서버가 바꾸거나 기본값으로 갈아치우면 안 된다
        assert fake_service.calls[0]["placeholder"] == "<!-- rown:page-break -->"
        assert fake_service.calls[0]["bytes"] == len(b"%PDF-1.4 x")

    def test_tmp_file_cleaned_up(self, client, fake_service):
        _upload(client)
        assert fake_service.tmp_existed_during_parse is True
        assert fake_service.seen_tmp_path is not None
        assert not fake_service.seen_tmp_path.exists()

    def test_tmp_file_cleaned_up_on_error(self, client, fake_service):
        fake_service.raise_exc = ParseTimeout("too slow")
        _upload(client)
        assert not fake_service.seen_tmp_path.exists()

    def test_missing_token_is_401(self, client):
        response = client.post(
            "/v1/parse", files={"file": ("doc.pdf", b"%PDF", "application/pdf")}
        )
        assert response.status_code == 401

    def test_disabled_is_501(self, client, monkeypatch):
        monkeypatch.setattr(gpu_main, "parse_service", None)
        assert _upload(client).status_code == 501

    def test_not_ready_is_503(self, client, fake_service):
        fake_service.ready = False
        assert _upload(client).status_code == 503

    def test_oversize_is_413_by_actual_bytes(self, client, monkeypatch):
        # Content-Length가 뭐라고 주장하든 실제 바이트 수로 자른다
        monkeypatch.setattr(
            gpu_main,
            "config",
            dataclasses.replace(gpu_main.config, token="testtoken", parse_max_bytes=8),
        )
        response = _upload(client, content=b"123456789")
        assert response.status_code == 413

    def test_empty_file_is_400(self, client):
        assert _upload(client, content=b"").status_code == 400

    def test_lane_saturated_is_429(self, client, fake_service):
        fake_service.raise_exc = GpuBusy(3, 240.0, 300.0)
        response = _upload(client)
        assert response.status_code == 429
        assert response.headers.get("Retry-After") == "5"

    def test_deadline_is_504(self, client, fake_service):
        fake_service.raise_exc = ParseTimeout("변환이 570초를 넘었습니다")
        assert _upload(client).status_code == 504


class TestHealthParseSection:
    def test_health_reports_parse_lane(self, client):
        body = client.get("/health").json()
        assert body["parse"]["ready"] is True
        assert body["parse"]["device"] == "cuda"
        assert body["parse"]["docling_version"] == "2.105.0"
        # 전용 레인 스냅샷 - 리랭킹·임베딩 queue와 분리돼 있어야 한다
        assert "estimated_wait_s" in body["parse"]["lane"]

    def test_health_parse_none_when_disabled(self, client, monkeypatch):
        monkeypatch.setattr(gpu_main, "parse_service", None)
        assert client.get("/health").json()["parse"] is None
