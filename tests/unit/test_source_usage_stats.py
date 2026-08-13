"""자료 사용 통계 집계 — count_numbers·build_source_usage 순수 로직 검증."""

import uuid

from src.core.citations import count_numbers
from src.db.models.project_source import ProjectSource
from src.db.models.section import Section
from src.services.stats.source_usage import build_source_usage


class TestCountNumbers:
    def test_counts_every_occurrence(self) -> None:
        content = "성장함 (출처 1). 확대됨 (출처 1, 2). 원문이다 [2]. 또 [1]"
        assert count_numbers(content) == {1: 3, 2: 2}

    def test_markdown_link_not_counted(self) -> None:
        # "[1](url)"은 링크 라벨이지 인용 마커가 아니다(MARK_RE 계약)
        assert count_numbers("본문 [1](https://a.b) 링크") == {}


def _source(title: str, source_type: str = "upload") -> ProjectSource:
    return ProjectSource(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        source_type=source_type,
        title=title,
        reliability="high",
        is_included=True,
    )


def _section(ch: int, sec: int, content: str) -> Section:
    return Section(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        chapter_number=ch,
        section_number=sec,
        chapter_title=f"{ch}장",
        title=f"{ch}.{sec}절",
        content=content,
    )


class TestBuildSourceUsage:
    def test_three_level_aggregation(self) -> None:
        sources = [_source("업로드A"), _source("웹B", "web_search"), _source("웹C", "web_search")]
        rows = [
            _section(1, 1, "가 (출처 1) 나 (출처 1, 2)"),  # 1→2회, 2→1회
            _section(1, 2, "다 [2]"),  # 2→1회
            _section(2, 1, "라 (출처 1)"),  # 1→1회
        ]
        out = build_source_usage(rows, sources)

        assert out.total_citations == 5
        # 사용된 자료는 참조 수 내림차순, 미사용(웹C)은 unused로 분리
        assert [(x.number, x.citations, x.sections_used) for x in out.sources] == [
            (1, 3, 2),
            (2, 2, 2),
        ]
        assert [x.title for x in out.unused] == ["웹C"]
        # 장별: 1장=4회(자료1·2), 2장=1회(자료1)
        assert [(c.chapter_number, c.citations, c.counts) for c in out.chapters] == [
            (1, 4, {1: 2, 2: 2}),
            (2, 1, {1: 1}),
        ]
        # 절별: 근거 다양성(n_sources)까지
        first = out.sections[0]
        assert (first.citations, first.n_sources, first.counts) == (3, 2, {1: 2, 2: 1})

    def test_out_of_range_numbers_dropped(self) -> None:
        # 채택 자료 1개인데 [7] — 조립이 지웠어야 할 잔재는 통계에서 버린다
        out = build_source_usage([_section(1, 1, "가 (출처 1) 나 [7]")], [_source("A")])
        assert out.total_citations == 1
        assert out.sections[0].counts == {1: 1}
