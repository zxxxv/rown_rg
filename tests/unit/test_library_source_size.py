"""라이브러리 목록의 자료 크기·페이지 — 0 B로만 보이던 문제.

업로드 자료는 본문(content_md) 없이 디스크에 파일로 있는데, 크기를 본문 길이로만
계산해 목록도 상위 폴더 합계도 0 B였다(2026-08-10 지적). 페이지 수는 파서만 알아서
색인 때 기록하지 않으면 영영 비어 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.api.routers.library import _source_file, _source_size
from src.db.models.project_source import ProjectSource


def _src(**kw) -> ProjectSource:
    row = ProjectSource(
        id=uuid4(),
        project_id=uuid4(),
        source_type=kw.pop("source_type", "upload"),
        title=kw.pop("title", "자료.pdf"),
        **kw,
    )
    row.created_at = datetime.now(UTC)
    return row


class TestSourceSize:
    def test_recorded_size_wins(self):
        row = _src(metadata_={"size_bytes": 12345})
        assert _source_size(row, "", {}) == 12345

    def test_upload_falls_back_to_disk(self, tmp_path: Path):
        f = tmp_path / "a.pdf"
        f.write_bytes(b"x" * 2048)
        # 옛 자료는 기록이 없다 - 재색인 없이도 목록에 크기가 보여야 한다.
        assert _source_size(_src(upload_path=str(f)), "", {}) == 2048

    def test_missing_upload_file_is_zero_not_crash(self):
        assert _source_size(_src(upload_path="/없는/경로.pdf"), "", {}) == 0

    def test_library_reference_uses_origin_node(self):
        node_id = uuid4()
        row = _src(source_type="library", library_node_id=node_id)
        assert _source_size(row, "", {node_id: 777}) == 777

    def test_web_source_uses_body_length(self):
        row = _src(source_type="web_search")
        assert _source_size(row, "본문", {}) == len("본문".encode())


class TestSourceFileMeta:
    def test_page_count_surfaces(self):
        row = _src(metadata_={"size_bytes": 10, "page_count": 42})
        node = _source_file(row, uuid4(), "관리자")
        assert node.file_meta is not None
        assert node.file_meta.page_count == 42
        assert node.file_meta.size_bytes == 10
