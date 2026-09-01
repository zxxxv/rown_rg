"""자료 단위 발간연도 — 색인 결과→자료 메타 복사와 게이트 목록 표시 파생."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from src.api.routers.projects import _index_meta, _to_source_item


def _result(**kw):
    base = dict(chunks_created=3, page_count=None, published_year=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _source_input():
    return SimpleNamespace(source_type="upload", file_path="없는파일.pdf")


class TestIndexMeta:
    def test_published_year_copied(self):
        meta = _index_meta(_source_input(), _result(published_year=2022))
        assert meta["published_year"] == 2022

    def test_unknown_year_omitted(self):
        meta = _index_meta(_source_input(), _result())
        assert "published_year" not in meta


def _row(source_type: str, meta: dict):
    return SimpleNamespace(
        id=uuid4(),
        source_type=source_type,
        title="자료",
        url="https://example.com" if source_type == "web_search" else None,
        reliability=None,
        is_included=True,
        library_node_id=None,
        metadata_=meta,
        created_at=datetime.now(UTC),
    )


class TestSourceItemYear:
    def test_file_source_reads_stored_year(self):
        item = _to_source_item(_row("upload", {"chunks": 3, "published_year": 2023}))
        assert item.published_year == 2023

    def test_web_source_derives_from_page_age(self):
        # 웹은 색인 전에도 수집이 준 page_age에서 연도를 파생해 보여준다.
        item = _to_source_item(_row("web_search", {"content_md": "", "page_age": "2024-08-01"}))
        assert item.published_year == 2024

    def test_unknown_stays_none(self):
        item = _to_source_item(_row("web_search", {"content_md": "", "page_age": "3 days ago"}))
        assert item.published_year is None
