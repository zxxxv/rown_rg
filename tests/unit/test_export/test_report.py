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
    export_report,
    markdown_to_blocks,
    report_blocks,
)


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
        # 인용 [n]은 전역 번호(출처장과 일치)라 본문에 유지된다(2026-08-05 정책 전환).
        assert any("[1]" in t for t in body_texts)
        assert any("17.1%" in t for t in body_texts)

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
        # 1장 정리표: SMR·KDI가 첫 등장 순서(오른쪽 단은 15개를 넘지 않아 빈칸).
        assert glossary_tables[0].rows == [
            ["SMR", "Small Modular Reactor", "", "", "", ""],
            ["KDI", "한국개발연구원", "", "", "", ""],
        ]
        # 2장 정리표: NEA만.
        assert glossary_tables[1].rows == [["NEA", "National Energy Agency", "", "", "", ""]]

    def test_glossary_descriptions_from_dict(self):
        """조립 시 생성한 약어 사전(glossary)이 있으면 설명 열이 채워진다."""
        glossary = {"SMR": {"full": "Small Modular Reactor", "desc": "소형 모듈 원자로"}}
        blocks = report_blocks(_state_with_abbreviations(), glossary)
        table = next(b for b in blocks if isinstance(b, Table) and b.headers[0] == "약어")
        assert ["SMR", "Small Modular Reactor", "소형 모듈 원자로", "", "", ""] in table.rows

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

    def test_figure_placeholder_only_for_tableless_sections(self):
        """표 있는 절엔 그림 없음, 표 없는 절엔 추천 시각자료 자리표시자 1개."""
        plan = [
            SectionPlan(chapter_number=1, section_number=1, title="표있음"),
            SectionPlan(chapter_number=1, section_number=2, title="표없음"),
        ]
        bodies = ["| 구분 | 값 |\n|---|---|\n| A | 1 |\n", "ㅇ 표 없는 서술 절임"]
        blocks = report_blocks(_state("자료 출처 검증", plan, bodies))
        captions = [b.caption for b in blocks if isinstance(b, Figure)]
        assert any("[그림 1-2]" in c for c in captions)  # 표 없는 절 → 그림
        assert not any("[그림 1-1]" in c for c in captions)  # 표 있는 절 → 그림 없음

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

    def test_no_sources_chapter_when_pool_empty(self):
        blocks = report_blocks(_state_with_selected_drafts())  # sources 없음
        assert not any(isinstance(b, Heading) and b.text == REFERENCES_HEADING for b in blocks)


class TestExportReport:
    def test_writes_hwpx_file(self, tmp_path: Path):
        state = _state_with_selected_drafts()
        path = export_report(state, output_dir=tmp_path)
        assert path == tmp_path / f"{state.project_id}.hwpx"
        assert path.exists()
        assert path.stat().st_size > 0
