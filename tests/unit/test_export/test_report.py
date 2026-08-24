"""보고서 HWPX 변환 검증 — 마크다운→블록 변환과 파일 렌더 (실DB·실LLM 없음)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from src.core.state import ProjectState
from src.core.types import (
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    SourceRef,
    SourceType,
)
from src.export.hwpx_writer import (
    Chart,
    Cover,
    Figure,
    Heading,
    PageBreak,
    Paragraph,
    Table,
    _is_group_start,
)
from src.services.export.report import (
    REFERENCES_HEADING,
    _figure_placeholder,
    _figures_needed,
    export_filename,
    export_report,
    markdown_to_blocks,
    report_blocks,
)


class TestFiguresNeeded:
    """분량이 요구하는 시각자료 수에서 표·차트가 채우고 남은 만큼이 그림 자리표시자가 된다.

    자리표시자는 key_points 수로 캡한다(없으면 1) — 소재 없이 늘리면 같은 제목·같은
    폴백 설명의 그림이 복제된다(2026-08-13 실사고: 한 절에 동일 그림 4개).
    """

    def test_short_section_needs_one_visual(self):
        assert _figures_needed("짧은 절", visual_count=0) == 1
        assert _figures_needed("짧은 절", visual_count=1) == 0

    def test_two_pages_need_two_visuals(self):
        content = "가" * 2400  # 1,200자당 1개 → 2개 필요
        kp = ["시장 규모", "경쟁 구도"]
        assert _figures_needed(content, visual_count=0, key_points=kp) == 2
        assert _figures_needed(content, visual_count=1, key_points=kp) == 1
        assert _figures_needed(content, visual_count=2, key_points=kp) == 0

    def test_surplus_visuals_do_not_go_negative(self):
        assert _figures_needed("짧은 절", visual_count=5) == 0

    def test_capped_by_key_points(self):
        content = "가" * 6000  # 분량상 5개 필요
        assert _figures_needed(content, visual_count=0, key_points=["a", "b"]) == 2

    def test_no_key_points_caps_at_one(self):
        # key_points 없는 절(폼에서 새로 추가한 절)은 폴백 설명이 하나뿐 — 1개만.
        content = "가" * 6000
        assert _figures_needed(content, visual_count=0) == 1
        assert _figures_needed(content, visual_count=1) == 1  # 분량상 부족분이 남아도 캡 1
        assert _figures_needed(content, visual_count=5) == 0  # 시각자료가 충분하면 0


class TestFigurePlaceholder:
    """한 절 그림 여러 개면 **캡션**이 key_point별로 달라야 한다(동일 복제 방지).

    설명은 캡션을 반복하지 않는 고정 문구다(2026-08-21 지적: 두 줄이 같은 문장을
    되풀이했다) — 구분은 캡션이 담당한다.
    """

    def test_captions_differ_per_key_point(self):
        plan = SectionPlan(
            chapter_number=3,
            section_number=2,
            title="국내 기술개발 동향",
            key_points=["주요 기업 R&D", "특허 출원 추이"],
        )
        f0 = _figure_placeholder(plan, 0)
        f1 = _figure_placeholder(plan, 1)
        assert f0.caption == "주요 기업 R&D"
        assert f1.caption == "특허 출원 추이"
        assert f0.caption != f1.caption

    def test_without_key_points_uses_section_title(self):
        plan = SectionPlan(chapter_number=3, section_number=2, title="국내 기술개발 동향")
        fig = _figure_placeholder(plan, 0)
        assert fig.caption == "국내 기술개발 동향"
        assert fig.description  # 설명은 존재하되 캡션을 반복하지 않는다

    def test_part_label_stripped_from_caption(self):
        # 프리셋 키포인트의 내부 파트 라벨 "(4-5-1)"이 캡션에 새지 않는다(2026-08-21).
        plan = SectionPlan(
            chapter_number=4,
            section_number=5,
            title="시사점",
            key_points=["(4-5-1) 국내 대응 과제 : 계층별 구분 제시"],
        )
        fig = _figure_placeholder(plan, 0)
        assert fig.caption == "국내 대응 과제 : 계층별 구분 제시"


class TestGroupStartSpacing:
    """ㅇ 그룹 간격 판정 — 하위 항목 뒤 새 ㅇ만 새 그룹으로 본다."""

    def test_new_group_after_subitems(self):
        # ㅇ(1)이 - (2)/* (3) 뒤에 오면 새 그룹 시작.
        assert _is_group_start(1, 2) is True
        assert _is_group_start(1, 3) is True

    def test_not_group_when_sibling_or_first(self):
        assert _is_group_start(1, 1) is False  # 연속된 형제 ㅇ
        assert _is_group_start(1, None) is False  # 문단 시작·헤딩 직후

    def test_non_o_levels_are_not_group_start(self):
        # □(0)은 _add_body가 따로 처리하므로 여기선 False. - / * 도 아님.
        assert _is_group_start(0, 2) is False
        assert _is_group_start(2, 1) is False
        assert _is_group_start(3, 2) is False


class TestMarkdownToBlocks:
    def test_headings_and_wrapped_paragraph(self):
        md = "# 소제목\n\n첫 문단 첫 줄\n둘째 줄\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [
            Heading(level=3, text="소제목"),
            Paragraph(text="첫 문단 첫 줄 둘째 줄"),
        ]

    def test_outline_markers_indented_per_level(self):
        md = "□ 대주제\nㅇ 소주제\n- 세부 항목\n* 보충 설명\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [
            Paragraph(text="□ 대주제", indent=0),
            Paragraph(text="ㅇ 소주제", indent=1),
            Paragraph(text="- 세부 항목", indent=2),
            Paragraph(text="* 보충 설명", indent=3),
        ]

    def test_outline_items_are_separate_paragraphs(self):
        # 마커 줄은 이어 붙지 않고 각각 독립 문단이 된다.
        blocks = markdown_to_blocks("ㅇ 첫 문장임\nㅇ 둘째 문장임\n")
        assert blocks == [
            Paragraph(text="ㅇ 첫 문장임", indent=1),
            Paragraph(text="ㅇ 둘째 문장임", indent=1),
        ]

    def test_invented_citation_markers_stripped(self):
        # 모델이 발명한 '[배경자료 제공됨]'류는 제거, 정상 [n]·[그림/표]·링크는 보존.
        md = "ㅇ 국내 이용률은 70.7%에 달함 [배경자료 제공됨] [1]\n"
        assert markdown_to_blocks(md) == [
            Paragraph(text="ㅇ 국내 이용률은 70.7%에 달함 [1]", indent=1)
        ]

    def test_figure_and_table_brackets_preserved(self):
        md = "ㅇ 추이는 [그림 1-1]과 [표 2]에 정리됨 [근거 없음 - 확인불가]\n"
        assert markdown_to_blocks(md) == [
            Paragraph(text="ㅇ 추이는 [그림 1-1]과 [표 2]에 정리됨", indent=1)
        ]

    def test_bold_marker_not_treated_as_outline(self):
        # "**"로 시작하는 강조는 개조식 '*' 마커가 아니다(마커는 뒤에 공백 필요).
        assert markdown_to_blocks("**핵심** 요약임") == [Paragraph(text="핵심 요약임")]

    def test_marker_heading_demoted_to_outline(self):
        """'## □ …'류 마커 헤딩은 개조식 문단으로 강등 — 위계 붕괴·마커 노출 방지."""
        md = "## □ 추진 배경\n### ㅇ 세부 동인\n#### - 사례\n"
        assert markdown_to_blocks(md) == [
            Paragraph(text="□ 추진 배경", indent=0),
            Paragraph(text="ㅇ 세부 동인", indent=1),
            Paragraph(text="- 사례", indent=2),
        ]

    def test_deep_plain_heading_becomes_top_outline(self):
        # 마커 없는 ###+ 소제목은 최상위 개조식 문단으로 렌더된다.
        assert markdown_to_blocks("### 데이터 수집\n") == [Paragraph(text="데이터 수집", indent=0)]

    def test_hr_skipped_blockquote_and_link_cleaned(self):
        md = "---\n> **주석**: 제약 사항임\n[출처](https://ex) 참고\n"
        assert markdown_to_blocks(md) == [
            Paragraph(text="주석: 제약 사항임", indent=1),
            Paragraph(text="출처 참고"),
        ]

    def test_gfm_table(self):
        md = "| 구분 | 값 |\n|---|---|\n| 고령화율 | 17.1% |\n| GDP | 3.2% |\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [
            Table(headers=["구분", "값"], rows=[["고령화율", "17.1%"], ["GDP", "3.2%"]])
        ]

    def test_table_cells_strip_inline_markdown(self):
        # 셀 안의 **강조**·[링크]는 벗겨 평문으로 (page 넘김·마커 노출 방지).
        md = "| **구분** | 값 |\n|---|---|\n| **Society** | [자료](http://x) |\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [Table(headers=["구분", "값"], rows=[["Society", "자료"]])]

    def test_chart_fence_becomes_chart_block(self):
        md = (
            "ㅇ 투자 규모는 아래와 같음\n\n"
            "```chart\ntype: bar\ntitle: 주요국 투자\nx: 미국 | 한국\n"
            "series: 투자액 = 120 | 30\n```\n"
        )
        blocks = markdown_to_blocks(md)
        assert [type(b).__name__ for b in blocks] == ["Paragraph", "Chart"]
        assert blocks[1].spec.title == "주요국 투자"
        assert blocks[1].spec.series[0].values == (120.0, 30.0)

    def test_unrenderable_chart_falls_back_to_original_table(self):
        # 값 개수가 x축과 어긋나 그릴 수 없다 - 보관해 둔 원본 표로 되돌린다.
        md = (
            "```chart\ntype: bar\nx: 미국 | 한국 | 중국\nseries: 투자액 = 120 | 30\n"
            "table: |\n  | 국가 | 투자액 |\n  |---|---|\n  | 미국 | 120 |\n```\n"
        )
        blocks = markdown_to_blocks(md)
        assert blocks == [Table(headers=["국가", "투자액"], rows=[["미국", "120"]], caption="")]

    def test_unrenderable_chart_without_table_is_dropped(self):
        # 되돌릴 표도 없으면 아무것도 남기지 않는다 - 스펙 원문이 본문에 노출되면 안 된다.
        md = "```chart\ntype: bar\nx: 미국 | 한국 | 중국\nseries: 투자액 = 120 | 30\n```\n"
        assert markdown_to_blocks(md) == []

    def test_table_caption_absorbed_from_preceding_line(self):
        # "표: 제목" 줄은 문단으로 남기지 않고 표의 caption으로 흡수한다(제목이 떠 있지 않게).
        md = "표: 주요국 개발 현황\n\n| 국가 | 노형 |\n|---|---|\n| 미국 | NuScale |\n"
        assert markdown_to_blocks(md) == [
            Table(headers=["국가", "노형"], rows=[["미국", "NuScale"]], caption="주요국 개발 현황")
        ]

    def test_table_caption_accepts_model_numbering_but_drops_it(self):
        # 모델이 번호까지 쓴 변형도 제목만 취한다 — 번호는 장 단위로 다시 매기기 때문.
        md = "[표 2-1] 주요국 현황\n| 국가 |\n|---|\n| 미국 |\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [Table(headers=["국가"], rows=[["미국"]], caption="주요국 현황")]

    def test_sentence_referring_to_table_is_not_a_caption(self):
        # "표 3.1에서 보듯이"류 서술은 제목이 아니다 — 문단으로 남아야 내용이 유실되지 않는다.
        md = "표 3.1에서 보듯이 격차가 큼\n| 국가 |\n|---|\n| 미국 |\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [
            Paragraph(text="표 3.1에서 보듯이 격차가 큼"),
            Table(headers=["국가"], rows=[["미국"]], caption=""),
        ]


def _state_with_selected_drafts() -> ProjectState:
    plan = [
        SectionPlan(chapter_number=1, section_number=1, title="개요"),
        SectionPlan(chapter_number=2, section_number=1, title="분석"),
    ]
    candidate_sets = []
    selections = {}
    for section, body in zip(
        plan,
        [
            "고령화율은 17.1%로 상승했다. (출처 1) 이는 전년 대비 증가다. (출처 2)",
            "## 세부 분석\n\n비용편익 비율은 1.2로 타당성이 있다. (출처 1)",
        ],
        strict=True,
    ):
        draft = SectionDraft(section_id=section.section_id, content=body, cited_chunk_ids=[uuid4()])
        candidate = SectionCandidate(draft=draft)
        candidate_sets.append(
            SectionCandidateSet(section_id=section.section_id, candidates=[candidate])
        )
        selections[section.section_id] = candidate.candidate_id
    return ProjectState(
        user_id=uuid4(),
        topic="인구 고령화 대응 방안",
        section_plan=plan,
        section_candidates=candidate_sets,
        section_selections=selections,
    )


def _state(
    topic: str,
    plan: list[SectionPlan],
    bodies: list[str],
    sources: list[SourceRef] | None = None,
) -> ProjectState:
    """plan+bodies로 '전부 선택된' ProjectState를 만든다(테스트 편의)."""
    candidate_sets, selections = [], {}
    for section, body in zip(plan, bodies, strict=True):
        draft = SectionDraft(section_id=section.section_id, content=body, cited_chunk_ids=[])
        cand = SectionCandidate(draft=draft)
        candidate_sets.append(SectionCandidateSet(section_id=section.section_id, candidates=[cand]))
        selections[section.section_id] = cand.candidate_id
    return ProjectState(
        user_id=uuid4(),
        topic=topic,
        section_plan=plan,
        section_candidates=candidate_sets,
        section_selections=selections,
        sources=sources or [],
    )


def _state_with_abbreviations() -> ProjectState:
    """약어가 장별로 흩어진 상태 — 1장(SMR·KDI), 2장(NEA)."""
    plan = [
        SectionPlan(chapter_number=1, section_number=1, title="개요"),
        SectionPlan(chapter_number=1, section_number=2, title="배경"),
        SectionPlan(chapter_number=2, section_number=1, title="전망"),
    ]
    bodies = [
        "ㅇ Small Modular Reactor(SMR)는 차세대 전원임 [1]",
        "ㅇ SMR 시장은 확대됨\nㅇ 한국개발연구원(KDI)의 전망임 [1]",
        "ㅇ National Energy Agency(NEA)는 2050년 전망을 발표함 [1]",
    ]
    candidate_sets = []
    selections = {}
    for section, body in zip(plan, bodies, strict=True):
        draft = SectionDraft(section_id=section.section_id, content=body, cited_chunk_ids=[uuid4()])
        candidate = SectionCandidate(draft=draft)
        candidate_sets.append(
            SectionCandidateSet(section_id=section.section_id, candidates=[candidate])
        )
        selections[section.section_id] = candidate.candidate_id
    return ProjectState(
        user_id=uuid4(),
        topic="원전 정책 전망",
        section_plan=plan,
        section_candidates=candidate_sets,
        section_selections=selections,
    )


class TestReportBlocks:
    def test_cover_toc_section_order_and_citation_strip(self):
        state = _state_with_selected_drafts()
        blocks = report_blocks(state)
        # 첫 장은 표지 — 제목은 개요 헤딩이 아니라 Cover 블록이라 목차·개요번호에 안 잡힌다.
        assert isinstance(blocks[0], Cover)
        assert blocks[0].title == "인구 고령화 대응 방안"
        assert not any(isinstance(b, Heading) and b.text == state.topic for b in blocks)
        # 표지 다음 쪽 나눔 후 목차 — 렌더되는 섹션이 번호·제목으로 나열된다.
        assert blocks[1] == PageBreak()
        assert blocks[2] == Heading(level=1, text="목차")
        toc_texts = [b.text for b in blocks if isinstance(b, Paragraph)]
        assert any(t.startswith("1.1") and "개요" in t for t in toc_texts)
        assert any(t.startswith("2.1") and "분석" in t for t in toc_texts)
        # 본문 섹션·헤딩은 목차 뒤에 이어진다.
        assert Heading(level=2, text="1.1 개요") in blocks
        assert Heading(level=2, text="2.1 분석") in blocks
        assert Heading(level=3, text="세부 분석") in blocks
        body_texts = [b.text for b in blocks if isinstance(b, Paragraph)]
        # 참고 표기 (출처 n)은 본문에서 걷어낸다 — 참고해 재작성한 문장에 번호를 인쇄할
        # 이유가 없다(2026-08-11). 출처는 참고문헌 최종장으로만 밝힌다.
        assert not any("출처 1" in t or "출처 2" in t for t in body_texts)
        assert any("고령화율은 17.1%로 상승했다." in t for t in body_texts)
        # 마커만 빠지고 문장은 그대로 — 앞 공백까지 걷어 "했다 ." 같은 자국을 남기지 않는다.
        assert any(t == "고령화율은 17.1%로 상승했다. 이는 전년 대비 증가다." for t in body_texts)

    def test_direct_quote_marker_survives_export(self):
        """직접 인용 [n]은 원문을 그대로 옮긴 문장이라 납품물에도 남는다."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="인용")]
        body = 'ㅇ 보고서는 "재편이 불가피"라고 밝힘 [2]\nㅇ 시장은 확대됨 (출처 2)'
        blocks = report_blocks(_state("직접 인용", plan, [body]))
        texts = [b.text for b in blocks if isinstance(b, Paragraph)]
        assert any('"재편이 불가피"라고 밝힘 [2]' in t for t in texts)
        assert any(t == "ㅇ 시장은 확대됨" for t in texts)

    def test_toc_lists_chapters_and_sections(self):
        """목차는 장(제N장)과 절(N.N)을 함께 계층으로 나열한다."""
        blocks = report_blocks(_state_with_selected_drafts())
        toc_start = blocks.index(Heading(level=1, text="목차"))
        # 목차 구간(목차 헤딩 ~ 다음 쪽 나눔) 안의 문단만 검사.
        after = blocks[toc_start + 1 :]
        end = next((i for i, b in enumerate(after) if isinstance(b, PageBreak)), len(after))
        toc = [b.text for b in after[:end] if isinstance(b, Paragraph)]
        assert "제1장" in toc and "제2장" in toc
        assert any(t.startswith("1.1") for t in toc)

    def test_toc_entries_point_at_body_headings(self):
        """목차 줄의 쪽번호 필드가 본문 제목의 책갈피와 짝이 맞는다.

        쪽번호 값은 문서를 여는 한컴이 채운다 — 우리가 보장할 것은 "가리키는 이름이
        실제로 본문에 있다"는 짝맞춤뿐이다. 짝이 어긋나면 한컴은 빈 칸이나 오류를 낸다.
        """
        blocks = report_blocks(_state_with_selected_drafts())
        refs = [b.page_ref for b in blocks if isinstance(b, Paragraph) and b.page_ref]
        # 표적은 두 곳에 있다 — 본문 제목(목차)과 표·그림 캡션(표 목차·그림 목차).
        marks = {b.bookmark for b in blocks if isinstance(b, Heading) and b.bookmark}
        marks |= {
            b.caption_bookmark
            for b in blocks
            if isinstance(b, Table | Figure | Chart) and b.caption_bookmark
        }
        assert refs, "목차 줄에 쪽번호 필드가 하나도 없다"
        assert len(refs) == len(set(refs)), "같은 책갈피를 두 줄이 가리킨다"
        assert set(refs) <= marks, f"본문에 없는 책갈피를 가리킨다: {set(refs) - marks}"

    def test_unselected_section_skipped(self):
        state = _state_with_selected_drafts()
        state = state.model_copy(update={"section_selections": {}})
        blocks = report_blocks(state)
        assert len(blocks) == 1
        assert isinstance(blocks[0], Cover)
        assert blocks[0].title == "인구 고령화 대응 방안"

    def test_per_chapter_glossary_at_chapter_end(self):
        """약어 정리는 각 장(1장·2장)마다 그 장 끝에 하나씩, 그 장 약어만 모은다."""
        blocks = report_blocks(_state_with_abbreviations())
        glossary_headings = [b for b in blocks if isinstance(b, Heading) and b.text == "약어 정리"]
        assert len(glossary_headings) == 2

        # 약어표는 2단(한 표에 15개 x 2 = 30개)이라 열이 6개다 - 3열짜리는 본문 폭을
        # 다 쓰면서 내용이 몇 글자뿐이라 페이지가 비어 보였다(2026-08-10).
        glossary_tables = [b for b in blocks if isinstance(b, Table) and b.headers[0] == "약어"]
        assert len(glossary_tables) == 2
        assert glossary_tables[0].headers == ["약어", "전체 명칭", "설명"] * 2
        # 남은 항목은 좌우로 반씩 나눈다 - 왼쪽부터 채우면 오른쪽 절반이 통째로 빈
        # 표가 나온다(2026-08-12 지적). SMR·KDI는 한 행에 나란히 놓인다.
        assert glossary_tables[0].rows == [
            ["SMR", "Small Modular Reactor", "", "KDI", "한국개발연구원", ""],
        ]
        # 2장 정리표: NEA 하나뿐이라 오른쪽 단은 빈칸.
        assert glossary_tables[1].rows == [["NEA", "National Energy Agency", "", "", "", ""]]
        # 열 폭은 표마다 계산하지 않고 고정한다 - 쪽을 넘길 때마다 폭이 달라지지 않게.
        assert glossary_tables[0].column_weights == glossary_tables[1].column_weights
        assert glossary_tables[0].column_weights is not None

    def test_glossary_descriptions_from_dict(self):
        """조립 시 생성한 약어 사전(glossary)이 있으면 설명 열이 채워진다."""
        glossary = {"SMR": {"full": "Small Modular Reactor", "desc": "소형 모듈 원자로"}}
        blocks = report_blocks(_state_with_abbreviations(), glossary)
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] == "약어")
        assert table.rows[0][:3] == ["SMR", "Small Modular Reactor", "소형 모듈 원자로"]

    def test_glossary_sits_at_chapter_end_before_next_chapter(self):
        """1장 약어 정리는 1장 본문 뒤·2장 헤딩 앞, 쪽 나눔 뒤(별도 페이지)에 놓인다."""
        blocks = report_blocks(_state_with_abbreviations())
        first_glossary = next(
            i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.text == "약어 정리"
        )
        ch1 = next(i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.text == "제1장")
        ch2 = next(i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.text == "제2장")
        assert ch1 < first_glossary < ch2
        # 별도 첨부 페이지 — 약어 정리 헤딩 바로 앞은 쪽 나눔이다(2026-08-05 확정).
        assert isinstance(blocks[first_glossary - 1], PageBreak)

    def test_korean_multiword_full_name_captured(self):
        """'월간 활성 사용자(MAU)'처럼 여러 어절 한글 풀네임이 통째로 잡힌다."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="지표")]
        bodies = ["ㅇ 월간 활성 사용자(MAU)는 증가함 [1]"]
        blocks = report_blocks(_state("플랫폼 지표", plan, bodies))
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] == "약어")
        assert table.rows[0][:2] == ["MAU", "월간 활성 사용자"]

    def test_chapters_start_on_new_page_with_chapter_heading(self):
        """챕터마다 쪽 나눔 + 장 헤딩('제N장 …', outline 제목 없으면 '제N장')."""
        blocks = report_blocks(_state_with_selected_drafts())
        # 본문 장 헤딩(Heading)만 — 목차의 '제N장'은 Paragraph라 걸리지 않는다.
        i1 = blocks.index(Heading(level=1, text="제1장"))
        i2 = blocks.index(Heading(level=1, text="제2장"))
        assert isinstance(blocks[i1 - 1], PageBreak)
        assert isinstance(blocks[i2 - 1], PageBreak)

    def test_duplicate_lead_heading_dropped(self):
        """본문이 '# 1.1 개요'로 시작하면 섹션 헤딩과 중복이라 제거된다."""
        state = _state_with_selected_drafts()
        sec = state.section_plan[0]
        dup_body = "# 1.1 개요\n\nㅇ 실제 내용 문장임"
        draft = SectionDraft(section_id=sec.section_id, content=dup_body, cited_chunk_ids=[])
        candidate = SectionCandidate(draft=draft)
        state = state.model_copy(
            update={
                "section_candidates": [
                    SectionCandidateSet(section_id=sec.section_id, candidates=[candidate]),
                    *[c for c in state.section_candidates if c.section_id != sec.section_id],
                ],
                "section_selections": {
                    **state.section_selections,
                    sec.section_id: candidate.candidate_id,
                },
            }
        )
        blocks = report_blocks(state)
        texts = [b.text for b in blocks if isinstance(b, Heading | Paragraph)]
        # 섹션 헤딩(1.1 개요)은 한 번만, 본문 쪽 중복 헤딩은 사라진다.
        assert texts.count("1.1 개요") == 1
        assert any("실제 내용" in t for t in texts)

    def test_short_section_with_table_needs_no_figure(self):
        """짧은 절은 시각자료 1개면 충분하다 - 표가 있으면 그림은 안 붙는다."""
        plan = [
            SectionPlan(chapter_number=1, section_number=1, title="표있음"),
            SectionPlan(chapter_number=1, section_number=2, title="표없음"),
        ]
        bodies = ["| 구분 | 값 |\n|---|---|\n| A | 1 |\n", "ㅇ 표 없는 서술 절임"]
        blocks = report_blocks(_state("자료 출처 검증", plan, bodies))
        captions = [b.caption for b in blocks if isinstance(b, Figure)]
        # 그림은 표 없는 절 하나에서만 나오고, 번호는 장 단위로 1부터 센다.
        assert captions == ["<그림 1-1> 표없음"]

    def test_long_section_gets_figure_even_with_table(self):
        """긴 절은 표가 있어도 그림이 함께 붙는다 - 표만 빽빽해지지 않게(2026-08-11)."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="긴 절")]
        body = "| 구분 | 값 |\n|---|---|\n| A | 1 |\n\n" + ("ㅇ 서술이 매우 긴 절임\n\n" * 300)
        blocks = report_blocks(_state("분량 기준", plan, [body]))
        figures = [b.caption for b in blocks if isinstance(b, Figure)]
        assert figures and figures[0] == "<그림 1-1> 긴 절"

    def test_figures_numbered_per_chapter_like_tables(self):
        """그림 번호도 장 안에서 이어지고 장이 바뀌면 1로 돌아간다."""
        plan = [
            SectionPlan(chapter_number=1, section_number=1, title="가"),
            SectionPlan(chapter_number=1, section_number=2, title="나"),
            SectionPlan(chapter_number=2, section_number=1, title="다"),
        ]
        blocks = report_blocks(_state("그림 번호", plan, ["ㅇ 서술임"] * 3))
        assert [b.caption for b in blocks if isinstance(b, Figure)] == [
            "<그림 1-1> 가",
            "<그림 1-2> 나",
            "<그림 2-1> 다",
        ]

    def test_table_captions_numbered_per_chapter(self):
        """표 번호는 장 안에서 이어지고 장이 바뀌면 1로 돌아간다."""
        plan = [
            SectionPlan(chapter_number=1, section_number=1, title="현황"),
            SectionPlan(chapter_number=1, section_number=2, title="전망"),
            SectionPlan(chapter_number=2, section_number=1, title="과제"),
        ]
        bodies = [
            "표: 국가별 투자\n| 국가 | 액수 |\n|---|---|\n| 미국 | 1 |\n",
            "표: 연도별 추이\n| 연도 | 값 |\n|---|---|\n| 2024년 | 2 |\n",
            "표: 주요 과제\n| 과제 | 비고 |\n|---|---|\n| 인력 | 3 |\n",
        ]
        blocks = report_blocks(_state("표 번호", plan, bodies))
        captions = [b.caption for b in blocks if isinstance(b, Table) and b.headers[0] != "약어"]
        assert captions == [
            "<표 1-1> 국가별 투자",
            "<표 1-2> 연도별 추이",
            "<표 2-1> 주요 과제",
        ]

    def test_table_without_caption_uses_headers(self):
        """제목이 없으면 머리행으로 세운다 - 절 제목만 쓰면 한 절의 표가 다 같은 이름이 된다."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="투자 현황")]
        body = "| 국가 | 투자액 | 시점 |\n|---|---|---|\n| 미국 | 1 | 2030년 |\n"
        blocks = report_blocks(_state("제목 누락", plan, [body]))
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] != "약어")
        assert table.caption == "<표 1-1> 국가별 투자액·시점"

    def test_generic_first_header_falls_back_to_section_title(self):
        """'구분'처럼 뜻 없는 첫 열은 분류축이 못 된다 - 절 제목을 앞에 두고 나머지 열을 잇는다."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="투자 현황")]
        body = "| 구분 | 정책 프로그램 | 예산 |\n|---|---|---|\n| A | 가 | 1 |\n"
        blocks = report_blocks(_state("일반어 머리행", plan, [body]))
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] != "약어")
        assert table.caption == "<표 1-1> 투자 현황: 정책 프로그램·예산"

    def test_single_column_table_keeps_section_title(self):
        """열이 하나뿐이면 머리행으로 제목을 세울 수 없다."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="투자 현황")]
        blocks = report_blocks(_state("한 열", plan, ["| 국가 |\n|---|\n| 미국 |\n"]))
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] != "약어")
        assert table.caption == "<표 1-1> 투자 현황"

    def test_table_unit_line_absorbed(self):
        """제목과 표 사이 "(단위: …)" 단독 줄은 표의 단위 줄로 흡수된다(작성 규칙 3종 세트)."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="예산")]
        body = "표: 연차별 예산\n(단위: 백만 원)\n| 연차 | 금액 |\n|---|---|\n| 1차 | 100 |\n"
        blocks = report_blocks(_state("단위 줄", plan, [body]))
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] != "약어")
        assert table.caption == "<표 1-1> 연차별 예산"
        assert table.unit == "(단위: 백만 원)"
        # 단위 줄이 본문 문단으로 남지 않는다.
        assert not any(isinstance(b, Paragraph) and "단위" in b.text for b in blocks)

    def test_table_source_line_becomes_bibliography(self):
        """표 바로 아래 (출처 n) 단독 줄은 걷어내지 않고 실서지 출처 줄로 변환된다."""
        sources = [
            SourceRef(
                id=uuid4(), source_type=SourceType.WEB_SEARCH, title="정부 통계", url="https://x"
            )
        ]
        plan = [SectionPlan(chapter_number=1, section_number=1, title="현황")]
        body = "표: 국가별 현황\n| 국가 | 값 |\n|---|---|\n| 미국 | 1 |\n\n(출처 1)\n"
        blocks = report_blocks(_state("표 출처", plan, [body], sources))
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] != "약어")
        assert table.source == "※ 출처: 정부 통계"
        # 표 밖 본문에는 출처 표기가 남지 않는다(참고문헌 목록 제외).
        body_end = blocks.index(Heading(level=1, text=REFERENCES_HEADING))
        assert not any(isinstance(b, Paragraph) and "출처 1" in b.text for b in blocks[:body_end])

    def test_table_source_label_variant_becomes_bibliography(self):
        """작성기의 라벨 변형("* 출처: (출처 n, m)")도 실서지로 변환된다.

        규약은 (출처 n) 단독 줄이지만 실제 출력엔 라벨·불릿이 붙었다(2026-08-15 실측).
        안 받으면 변환이 통째로 빠지고 마커만 걷힌 빈 '출처:' 껍데기가 표 밑에 남는다.
        """
        sources = [
            SourceRef(
                id=uuid4(), source_type=SourceType.WEB_SEARCH, title="정부 통계", url="https://x"
            ),
            SourceRef(id=uuid4(), source_type=SourceType.UPLOAD, title="실태 조사", url=None),
        ]
        plan = [SectionPlan(chapter_number=1, section_number=1, title="현황")]
        body = "표: 국가별 현황\n| 국가 | 값 |\n|---|---|\n| 미국 | 1 |\n\n* 출처: (출처 1, 2)\n"
        blocks = report_blocks(_state("표 출처 변형", plan, [body], sources))
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] != "약어")
        assert table.source == "※ 출처: 정부 통계; 실태 조사"
        # 라벨 껍데기("* 출처:")가 본문 문단으로 남지 않는다.
        body_end = blocks.index(Heading(level=1, text=REFERENCES_HEADING))
        assert not any(
            isinstance(b, Paragraph) and b.text.strip().startswith(("출처", "* 출처"))
            for b in blocks[:body_end]
        )

    def test_year_quote_normalized(self):
        """연도 어깨점 혼용(‘24·`24)은 오른쪽 홑따옴표(’24)로 정규화된다."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="연혁")]
        blocks = report_blocks(_state("연도", plan, ["ㅇ ‘24년 실적은 `23년 대비 증가함"]))
        texts = [b.text for b in blocks if isinstance(b, Paragraph)]
        assert any("’24년" in t and "’23년" in t for t in texts)

    def test_visual_index_lists_numbered_captions(self):
        """표 목차·그림 목차가 본문 캡션 그대로 목차 뒤에 나열된다."""
        plan = [SectionPlan(chapter_number=1, section_number=1, title="현황")]
        body = "표: 국가별 투자\n| 국가 | 액수 |\n|---|---|\n| 미국 | 1 |\n"
        blocks = report_blocks(_state("표 목차", plan, [body]))
        table_index = blocks.index(Heading(level=1, text="표 목차"))
        first_chapter = blocks.index(Heading(level=1, text="제1장"))
        assert blocks.index(Heading(level=1, text="목차")) < table_index < first_chapter
        entries = [
            b.text for b in blocks[table_index + 1 : first_chapter] if isinstance(b, Paragraph)
        ]
        assert "<표 1-1> 국가별 투자" in entries

    def test_no_summary_page_even_with_stored_summary(self):
        """요약문은 r6에서 제거 — 옛 프로젝트의 config["summary"]가 남아 있어도 렌더하지 않는다."""
        summary = {
            "chapters": [{"number": 1, "title": "개요", "lines": ["(배경) 고령화가 가속됨"]}]
        }
        state = _state_with_selected_drafts().model_copy(update={"options": {"summary": summary}})
        blocks = report_blocks(state)
        assert not any(isinstance(b, Heading) and b.text == "요약문" for b in blocks)

    def test_sources_final_chapter(self):
        sources = [
            SourceRef(
                id=uuid4(),
                source_type=SourceType.WEB_SEARCH,
                title="웹 자료 제목",
                url="https://ex.com/a",
            ),
            SourceRef(id=uuid4(), source_type=SourceType.UPLOAD, title="업로드 문서"),
        ]
        state = _state_with_selected_drafts().model_copy(update={"sources": sources})
        blocks = report_blocks(state)
        src_idx = next(
            i
            for i, b in enumerate(blocks)
            if isinstance(b, Heading) and b.text == REFERENCES_HEADING
        )
        # 참고문헌은 마지막 장 — 뒤에 다른 장/절 헤딩이 없다.
        assert [b for b in blocks[src_idx + 1 :] if isinstance(b, Heading)] == []
        entries = [b.text for b in blocks[src_idx + 1 :] if isinstance(b, Paragraph)]
        assert any("웹 자료 제목" in t and "https://ex.com/a" in t for t in entries)
        assert any("업로드 문서" in t for t in entries)
        # 본문에서는 [n]을 걷어내도 참고문헌 목록의 번호는 남는다 — 목록 번호이지 인용이 아니다.
        assert entries[0].startswith("[1] ")

    def test_no_sources_chapter_when_pool_empty(self):
        blocks = report_blocks(_state_with_selected_drafts())  # sources 없음
        assert not any(isinstance(b, Heading) and b.text == REFERENCES_HEADING for b in blocks)


class TestExportReport:
    def test_writes_hwpx_file(self, tmp_path: Path):
        state = _state_with_selected_drafts()
        path = export_report(state, output_dir=tmp_path)
        # 파일명에 렌더 버전이 붙는다 — 코드가 바뀌면 옛 산출물이 캐시로 재사용되지 않게.
        assert path == tmp_path / export_filename(state.project_id)
        assert path.exists()
        assert path.stat().st_size > 0
