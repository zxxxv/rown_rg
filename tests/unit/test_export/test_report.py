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
)
from src.export.hwpx_writer import Heading, PageBreak, Paragraph, Table
from src.services.export.report import export_report, markdown_to_blocks, report_blocks


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
            "고령화율은 17.1%로 상승했다. [1] 이는 전년 대비 증가다. [2]",
            "## 세부 분석\n\n비용편익 비율은 1.2로 타당성이 있다. [1]",
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
    def test_title_toc_section_order_and_citation_strip(self):
        state = _state_with_selected_drafts()
        blocks = report_blocks(state)
        assert blocks[0] == Heading(level=1, text="인구 고령화 대응 방안")
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
        assert all("[1]" not in t and "[2]" not in t for t in body_texts)
        assert any("17.1%" in t for t in body_texts)

    def test_unselected_section_skipped(self):
        state = _state_with_selected_drafts()
        state = state.model_copy(update={"section_selections": {}})
        blocks = report_blocks(state)
        assert blocks == [Heading(level=1, text="인구 고령화 대응 방안")]

    def test_per_chapter_glossary_appended_at_chapter_end(self):
        blocks = report_blocks(_state_with_abbreviations())
        # "약어 정리" 헤딩이 각 장(1장, 2장)마다 하나씩 = 두 번 나온다.
        glossary_headings = [
            i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.text == "약어 정리"
        ]
        assert len(glossary_headings) == 2

        glossary_tables = [
            b for b in blocks if isinstance(b, Table) and b.headers == ["약어", "전체 명칭"]
        ]
        assert len(glossary_tables) == 2
        # 1장 정리표: SMR·KDI가 첫 등장 순서로, 장을 넘나든 중복 없이 한 번씩.
        assert glossary_tables[0].rows == [
            ["SMR", "Small Modular Reactor"],
            ["KDI", "한국개발연구원"],
        ]
        # 2장 정리표: NEA만.
        assert glossary_tables[1].rows == [["NEA", "National Energy Agency"]]

    def test_chapters_start_on_new_page_with_chapter_heading(self):
        """챕터마다 쪽 나눔 + 장 헤딩('제N장 …', outline 제목 없으면 '제N장')."""
        blocks = report_blocks(_state_with_selected_drafts())
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

    def test_glossary_flushes_before_next_chapter(self):
        blocks = report_blocks(_state_with_abbreviations())
        first_glossary = next(
            i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.text == "약어 정리"
        )
        chapter2_heading = next(
            i for i, b in enumerate(blocks) if isinstance(b, Heading) and b.text == "2.1 전망"
        )
        # 1장 약어 정리는 2장 시작 전에 놓인다.
        assert first_glossary < chapter2_heading


class TestExportReport:
    def test_writes_hwpx_file(self, tmp_path: Path):
        state = _state_with_selected_drafts()
        path = export_report(state, output_dir=tmp_path)
        assert path == tmp_path / f"{state.project_id}.hwpx"
        assert path.exists()
        assert path.stat().st_size > 0
