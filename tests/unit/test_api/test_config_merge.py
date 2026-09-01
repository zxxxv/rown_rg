"""옵션 전체 교체의 내부 키 보존 — merge_config_update (실DB 없음).

폼은 config 전체를 되돌려 보내므로, 파이프라인이 남긴 내부 키(취소 복귀 지점·
검증 완료 표시·모델 스냅샷)는 교체에서 살아남아야 하고, 클라이언트가 낡은
사본을 되돌려 보내도 서버 값이 이겨야 한다.
"""

from __future__ import annotations

from src.api.routers.projects import merge_config_update


class TestMergeConfigUpdate:
    def test_normal_keys_replaced(self):
        merged = merge_config_update(
            {"model_mode": "standard", "hyde_enabled": False},
            {"model_mode": "premium", "hyde_enabled": True},
        )
        assert merged == {"model_mode": "premium", "hyde_enabled": True}

    def test_internal_keys_survive_replacement(self):
        """폼이 안 보내는 내부 키는 전체 교체 후에도 남는다."""
        current = {
            "model_mode": "premium",
            "cancelled_from": "writing",
            "verify_resolved": ["k1"],
            "models": {"research": "claude-sonnet-4-6", "write": "claude-opus-5"},
        }
        merged = merge_config_update(current, {"model_mode": "premium", "rules": []})
        assert merged["cancelled_from"] == "writing"
        assert merged["verify_resolved"] == ["k1"]
        assert merged["models"] == {"research": "claude-sonnet-4-6", "write": "claude-opus-5"}
        assert merged["rules"] == []

    def test_server_wins_on_internal_key_roundtrip(self):
        """클라이언트가 낡은 스냅샷을 되돌려 보내도 서버 값이 진실."""
        current = {"models": {"research": "claude-sonnet-4-6", "write": "claude-opus-5"}}
        merged = merge_config_update(
            current,
            {"model_mode": "premium", "models": {"research": "stale", "write": "stale"}},
        )
        assert merged["models"] == {"research": "claude-sonnet-4-6", "write": "claude-opus-5"}

    def test_no_internal_keys_passthrough(self):
        """서버에 내부 키가 없으면(스냅샷 도입 전 프로젝트) 들어온 값 그대로."""
        merged = merge_config_update(None, {"model_mode": "economy"})
        assert merged == {"model_mode": "economy"}
