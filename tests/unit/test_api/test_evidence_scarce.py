"""'자료 부족' 배지 판정 — 캡 도달은 부족이 아니다(2026-08-27 철강 런 실측).

절당 근거는 검색 캡(retrieval_top_k=24)이 상한이라, 분량 목표가 캡×750자를
넘는 프리셋에선 만점 검색에도 volume_scaled가 기계적으로 켜져 1~4장 전부에
배지가 떴다. 배지는 "근거가 캡 미만으로 깎인 절"에만 단다.
"""

from __future__ import annotations

from src.api.routers.projects import _is_evidence_scarce
from src.core.config import settings


class TestEvidenceScarce:
    def test_scaled_below_cap_is_scarce(self) -> None:
        assert _is_evidence_scarce({"volume_scaled": True, "evidence_count": 14})

    def test_scaled_at_cap_is_not_scarce(self) -> None:
        cap = settings.retrieval_top_k
        assert not _is_evidence_scarce({"volume_scaled": True, "evidence_count": cap})
        # 다중 관점 절은 캡을 넘길 수 있다 - 당연히 부족 아님.
        assert not _is_evidence_scarce({"volume_scaled": True, "evidence_count": cap + 51})

    def test_not_scaled_never_scarce(self) -> None:
        assert not _is_evidence_scarce({"volume_scaled": False, "evidence_count": 3})
        assert not _is_evidence_scarce(None)

    def test_old_sections_without_count_keep_flag(self) -> None:
        # 근거수 기록이 없는 옛 절은 volume_scaled를 그대로 따른다(정보 없음 = 유지).
        assert _is_evidence_scarce({"volume_scaled": True})
