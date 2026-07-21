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
from src.export.hwpx_writer import Heading, Paragraph, Table
from src.services.export.report import export_report, markdown_to_blocks, report_blocks


class TestMarkdownToBlocks:
    def test_headings_paragraphs_bullets(self):
        md = "# 소제목\n\n첫 문단 첫 줄\n둘째 줄\n\n- 항목 하나\n- 항목 둘\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [
            Heading(level=3, text="소제목"),
            Paragraph(text="첫 문단 첫 줄 둘째 줄"),
            Paragraph(text="· 항목 하나"),
            Paragraph(text="· 항목 둘"),
        ]

    def test_gfm_table(self):
        md = "| 구분 | 값 |\n|---|---|\n| 고령화율 | 17.1% |\n| GDP | 3.2% |\n"
        blocks = markdown_to_blocks(md)
        assert blocks == [
            Table(headers=["구분", "값"], rows=[["고령화율", "17.1%"], ["GDP", "3.2%"]])
        ]

    def test_bold_markers_removed(self):
        blocks = markdown_to_blocks("**핵심** 요약입니다.")
        assert blocks == [Paragraph(text="핵심 요약입니다.")]


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


class TestReportBlocks:
    def test_title_section_order_and_citation_strip(self):
        state = _state_with_selected_drafts()
        blocks = report_blocks(state)
        assert blocks[0] == Heading(level=1, text="인구 고령화 대응 방안")
        assert Heading(level=2, text="1.1 개요") in blocks
        assert Heading(level=2, text="2.1 분석") in blocks
        assert Heading(level=3, text="세부 분석") in blocks
        texts = [b.text for b in blocks if isinstance(b, Paragraph)]
        assert all("[1]" not in t and "[2]" not in t for t in texts)
        assert any("17.1%" in t for t in texts)

    def test_unselected_section_skipped(self):
        state = _state_with_selected_drafts()
        state = state.model_copy(update={"section_selections": {}})
        blocks = report_blocks(state)
        assert blocks == [Heading(level=1, text="인구 고령화 대응 방안")]


class TestExportReport:
    def test_writes_hwpx_file(self, tmp_path: Path):
        state = _state_with_selected_drafts()
        path = export_report(state, output_dir=tmp_path)
        assert path == tmp_path / f"{state.project_id}.hwpx"
        assert path.exists()
        assert path.stat().st_size > 0
