"""청크 → PDF 페이지 매핑 - 마커 제거·페이지 배정의 결정적 로직.

파서(pdf.py)가 페이지 사이에 심은 마커를 색인이 걷어내고, 각 청크가 몇 쪽에서
왔는지 되짚는다. 페이지는 위치 안내라 근사면 충분하지만, 틀린 쪽을 가리키면
안 되므로 못 찾으면 None이 계약이다.
"""

from __future__ import annotations

from src.clients.parser.base import PAGE_BREAK_MARKER
from src.clients.parser.pdf import PdfParser
from src.services.indexing._pages import assign_chunk_pages, strip_page_markers


class TestStripPageMarkers:
    def test_no_marker_returns_single_page(self):
        md = "마커 없는 웹·HWPX 본문"
        clean, starts = strip_page_markers(md)
        assert clean == md
        assert starts == [0]

    def test_markers_removed_and_offsets_recorded(self):
        md = f"1쪽 본문{PAGE_BREAK_MARKER}2쪽 본문{PAGE_BREAK_MARKER}3쪽 본문"
        clean, starts = strip_page_markers(md)
        assert PAGE_BREAK_MARKER not in clean
        assert clean == "1쪽 본문2쪽 본문3쪽 본문"
        assert len(starts) == 3
        assert clean[starts[1] :].startswith("2쪽")
        assert clean[starts[2] :].startswith("3쪽")

    def test_parser_postprocess_keeps_marker(self):
        # 파서 후처리(숫자 단독 줄 제거·빈 표 필터·깨진 문자 제거)가 마커를 지우면
        # 그 자료는 조용히 페이지 없이 색인된다 - 후처리와 마커의 계약을 못 박는다.
        md = f"본문 한 줄\n\n{PAGE_BREAK_MARKER}\n\n다음 쪽 본문"
        assert PAGE_BREAK_MARKER in PdfParser._postprocess(md)


class TestAssignChunkPages:
    def test_assigns_by_position(self):
        p1 = "첫 페이지의 본문 문단이다. 여기 내용이 있다.\n"
        p2 = "두 번째 페이지의 본문 문단이다. 다른 내용이 있다.\n"
        clean = p1 + p2
        starts = [0, len(p1)]
        assert assign_chunk_pages([p1.strip(), p2.strip()], clean, starts) == [1, 2]

    def test_pageless_document_gets_none(self):
        # 마커 없는 문서(page_starts 길이 1)는 페이지를 지어내지 않는다.
        assert assign_chunk_pages(["아무 본문 한 문단이다"], "아무 본문 한 문단이다", [0]) == [None]

    def test_unfindable_chunk_gets_none(self):
        clean = "첫 페이지 본문이 길게 이어진다."
        assert assign_chunk_pages(["원문에 없는 재구성 문장이다"], clean, [0, 10]) == [None]

    def test_short_probe_skipped(self):
        # 8자 미만 첫 줄(표 조각 등)은 오매칭 위험이 커서 페이지를 달지 않는다.
        clean = "표\n두 번째 페이지의 본문 문단."
        assert assign_chunk_pages(["표"], clean, [0, 2]) == [None]

    def test_repeated_heading_respects_document_order(self):
        line = "반복되는 동일한 소제목 문장이다"
        p1 = line + "\n1쪽 고유 내용\n"
        p2 = line + "\n2쪽 고유 내용\n"
        clean = p1 + p2
        starts = [0, len(p1)]
        # 두 청크가 같은 첫 줄로 시작해도 커서 전진으로 1쪽·2쪽이 갈려야 한다.
        assert assign_chunk_pages([p1.strip(), p2.strip()], clean, starts) == [1, 2]
