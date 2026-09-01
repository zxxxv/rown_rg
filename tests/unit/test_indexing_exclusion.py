"""0청크 자료 자동 제외(apply_index_outcome) — 채택 상태 전이 규칙.

핵심 계약: 0청크 → 자동 제외(+이유 메시지), 재색인 성공 → 자동 제외만 복구.
사람이 제외한 자료(auto_excluded 없음)는 청크가 생겨도 건드리지 않는다.
"""

from __future__ import annotations

from src.db.models.project_source import ProjectSource
from src.services.indexing.exclusion import AUTO_EXCLUDED_KEY, apply_index_outcome


def _source(**kwargs) -> ProjectSource:
    row = ProjectSource(source_type="upload", is_included=True, metadata_={})
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


class TestApplyIndexOutcome:
    def test_zero_chunks_excludes_with_reason(self):
        row = _source()
        apply_index_outcome(row, 0)
        assert row.is_included is False
        assert row.metadata_[AUTO_EXCLUDED_KEY] is True
        # 사용자가 목록에서 이유를 볼 수 있어야 한다 - 기존 오류 표시 경로를 재사용
        assert row.metadata_["index_error"]

    def test_zero_chunks_keeps_existing_error_message(self):
        row = _source(metadata_={"index_error": "파싱 실패"})
        apply_index_outcome(row, 0)
        assert row.is_included is False
        assert row.metadata_["index_error"] == "파싱 실패"

    def test_reindex_success_restores_auto_excluded(self):
        row = _source(is_included=False, metadata_={AUTO_EXCLUDED_KEY: True})
        apply_index_outcome(row, 12)
        assert row.is_included is True
        assert AUTO_EXCLUDED_KEY not in row.metadata_

    def test_human_exclusion_survives_reindex(self):
        # 사람이 제외한 행에는 auto_excluded가 없다 - 색인이 성공해도 그대로 둔다.
        row = _source(is_included=False, metadata_={})
        apply_index_outcome(row, 12)
        assert row.is_included is False

    def test_success_on_included_row_is_noop(self):
        row = _source(metadata_={"chunks": 3})
        apply_index_outcome(row, 3)
        assert row.is_included is True
        assert row.metadata_ == {"chunks": 3}
