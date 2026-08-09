"""섹션 인용 — [N] 번호 추출과 전역 번호 직해석(순수 로직, DB·라우팅 없음).

전역 번호화(2026-08-05) 이후 본문 [n] = 채택 자료 수집 순서 n번째(출처 최종장과
동일 단일 진실). 미리보기 인용 목록은 그 순서를 그대로 읽는다.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.api.routers.projects import _citation_numbers, _citations_from_numbers


def _src(title: str, url: str | None = None, reliability: str | None = None):
    return SimpleNamespace(id=f"id-{title}", title=title, url=url, reliability=reliability)


class TestCitationNumbers:
    def test_order_preserved_and_deduped(self):
        content = "성장률 3.2%다 [2]. 이는 상승세다 [1]. 재인용 [2]과 추가 [7]."
        assert _citation_numbers(content) == [2, 1, 7]

    def test_no_markers(self):
        assert _citation_numbers("인용 없는 본문") == []

    def test_ref_style_markers_ignored(self):
        # 숫자 아닌 마커([ref:x], [표 1])는 인용이 아니다.
        assert _citation_numbers("[ref:src_a] 참고, [표 1] 아님, [3]만 인용") == [3]


class TestCitationsFromNumbers:
    def test_global_numbers_resolve_in_ascending_order(self):
        sources = [_src("자료A", "https://a"), _src("자료B"), _src("자료C", None, "high")]
        rows = _citations_from_numbers([3, 1], sources)
        # 목록은 번호 오름차순 — 참고문헌처럼 읽힌다.
        assert [(r.number, r.title) for r in rows] == [(1, "자료A"), (3, "자료C")]
        assert rows[0].url == "https://a"
        assert rows[1].reliability == "high"

    def test_out_of_range_number_marked_unknown(self):
        rows = _citations_from_numbers([1, 99], [_src("자료A")])
        assert rows[0].title == "자료A"
        assert rows[1].number == 99
        assert "불명" in rows[1].title
        assert rows[1].source_id is None

    def test_empty(self):
        assert _citations_from_numbers([], []) == []
