"""GPU 리랭킹 서비스 HTTP 계약.

앱이 이 응답을 ``zip(hits, scores, strict=True)``로 묶으므로 **개수와 순서**가
계약의 전부다. 인증은 fail-closed여야 한다 - 이 엔드포인트로 자료 본문이 오간다.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from gpu_service.app import main as gpu_main


class _FakeEncoder:
    """모델 없이 서비스 계층만 검증 — 점수는 입력 순서를 드러내는 값으로."""

    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

    def score(self, query: str, passages: list[str], *, batch_size: int | None = None):
        return [round(1.0 - i * 0.01, 4) for i in range(len(passages))]


@pytest.fixture
def loaded(monkeypatch):
    monkeypatch.setattr(gpu_main, "config", dataclasses.replace(gpu_main.config, token="testtoken"))
    monkeypatch.setattr(gpu_main.service, "load", lambda: None)
    monkeypatch.setattr(gpu_main.service, "_encoder", _FakeEncoder())
    with TestClient(gpu_main.app) as client:
        yield client


@pytest.fixture
def unloaded(monkeypatch):
    monkeypatch.setattr(gpu_main, "config", dataclasses.replace(gpu_main.config, token="testtoken"))
    monkeypatch.setattr(gpu_main.service, "load", lambda: None)
    monkeypatch.setattr(gpu_main.service, "_encoder", None)
    with TestClient(gpu_main.app) as client:
        yield client


AUTH = {"Authorization": "Bearer testtoken"}


class TestHealth:
    def test_open_without_token(self, loaded):
        # 터널 헬스체크가 토큰 없이 찔러야 한다. 드러나는 정보는 설정값뿐.
        response = loaded.get("/health")
        assert response.status_code == 200
        assert response.json()["ready"] is True

    def test_reports_actual_providers(self, loaded):
        body = loaded.get("/health").json()
        # CUDA를 요청했는데 조용히 CPU로 떨어지는 사고를 여기서 드러낸다.
        assert body["on_gpu"] is True
        assert "CUDAExecutionProvider" in body["providers"]


class TestAuth:
    def test_missing_header_is_401(self, loaded):
        response = loaded.post("/v1/rerank", json={"query": "q", "passages": ["a"]})
        assert response.status_code == 401

    def test_wrong_token_is_401(self, loaded):
        response = loaded.post(
            "/v1/rerank",
            json={"query": "q", "passages": ["a"]},
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401

    def test_anon_allowed_only_when_explicitly_enabled(self, monkeypatch):
        monkeypatch.setattr(
            gpu_main,
            "config",
            dataclasses.replace(gpu_main.config, token="", allow_anon=True),
        )
        monkeypatch.setattr(gpu_main.service, "load", lambda: None)
        monkeypatch.setattr(gpu_main.service, "_encoder", _FakeEncoder())
        with TestClient(gpu_main.app) as client:
            response = client.post("/v1/rerank", json={"query": "q", "passages": ["a"]})
        assert response.status_code == 200


class TestRerank:
    def test_scores_match_passage_count_and_order(self, loaded):
        passages = [f"p{i}" for i in range(7)]
        response = loaded.post(
            "/v1/rerank", json={"query": "q", "passages": passages}, headers=AUTH
        )
        assert response.status_code == 200
        scores = response.json()["scores"]
        assert len(scores) == len(passages)
        assert scores == [round(1.0 - i * 0.01, 4) for i in range(7)]

    def test_empty_passages_rejected(self, loaded):
        response = loaded.post("/v1/rerank", json={"query": "q", "passages": []}, headers=AUTH)
        assert response.status_code == 422

    def test_over_limit_is_413(self, loaded, monkeypatch):
        monkeypatch.setattr(
            gpu_main,
            "config",
            dataclasses.replace(gpu_main.config, token="testtoken", max_passages=3),
        )
        response = loaded.post(
            "/v1/rerank", json={"query": "q", "passages": ["a", "b", "c", "d"]}, headers=AUTH
        )
        assert response.status_code == 413

    def test_unloaded_model_is_503(self, unloaded):
        response = unloaded.post("/v1/rerank", json={"query": "q", "passages": ["a"]}, headers=AUTH)
        # 앱은 503을 실패로 보고 폴백한다 - 조용히 빈 점수를 주는 것보다 낫다.
        assert response.status_code == 503


class TestConfigValidation:
    def test_refuses_to_start_without_token(self):
        from gpu_service.app.config import ServiceConfig

        config = ServiceConfig(
            model_dir="/models/x",
            device="cuda",
            batch_size=32,
            max_length=512,
            token="",
            allow_anon=False,
            max_passages=512,
            max_concurrency=1,
        )
        with pytest.raises(RuntimeError, match="GPU_TOKEN"):
            config.validate()

    def test_rejects_unknown_device(self):
        from gpu_service.app.config import ServiceConfig

        config = ServiceConfig(
            model_dir="/models/x",
            device="mps",
            batch_size=32,
            max_length=512,
            token="t",
            allow_anon=False,
            max_passages=512,
            max_concurrency=1,
        )
        with pytest.raises(RuntimeError, match="GPU_DEVICE"):
            config.validate()


class TestRequestLogging:
    """미들웨어가 추론 요청을 결과와 함께 남기는지 — 성공만이 아니라 거절도."""

    @pytest.fixture
    def logged(self, loaded, tmp_path, monkeypatch):
        from gpu_service.app.request_log import RequestLog

        reqlog = RequestLog(str(tmp_path))
        monkeypatch.setattr(gpu_main, "_reqlog", reqlog)
        return loaded, reqlog

    def _events(self, reqlog):
        from datetime import datetime

        from gpu_service.app.request_log import KST

        return reqlog.read_day(datetime.now(KST).strftime("%Y-%m-%d"))

    def test_success_logged_with_detail(self, logged):
        client, reqlog = logged
        client.post("/v1/rerank", json={"query": "q", "passages": ["a", "b"]}, headers=AUTH)
        events = self._events(reqlog)
        assert len(events) == 1
        assert events[0]["endpoint"] == "rerank"
        assert events[0]["code"] == 200
        assert events[0]["n"] == 2
        assert events[0]["device"] == "cuda"
        assert events[0]["ms"] >= 0

    def test_rejection_logged_too(self, logged):
        # 401도 남는다 - "그날 무슨 일이 있었나"에는 거절당한 시도도 포함이다.
        client, reqlog = logged
        client.post("/v1/rerank", json={"query": "q", "passages": ["a"]})
        events = self._events(reqlog)
        assert len(events) == 1
        assert events[0]["code"] == 401

    def test_health_polling_not_logged(self, logged):
        client, reqlog = logged
        client.get("/health")
        client.get("/stats/history")
        assert self._events(reqlog) == []

    def test_stats_endpoints_serve_the_log(self, logged):
        client, reqlog = logged
        client.post("/v1/rerank", json={"query": "q", "passages": ["a"]}, headers=AUTH)

        days = client.get("/stats/days").json()
        assert days["enabled"] is True
        assert days["days"][0]["total"] == 1

        daily = client.get(f"/stats/daily?date={days['days'][0]['date']}").json()
        assert daily["events"][0]["endpoint"] == "rerank"

        assert client.get("/stats/daily?date=bogus").status_code == 400

    def test_view_page_served(self, loaded):
        response = loaded.get("/stats/view")
        assert response.status_code == 200
        assert "GPU 요청 기록" in response.text
