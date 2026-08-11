"""정적 게이트 검사의 결정성 검증 — 순수 함수라 DB·LLM 없이 완결."""

from __future__ import annotations

from uuid import UUID, uuid4

from src.core.types import (
    CheckSeverity,
    GateResult,
    RetrievedChunk,
    SectionDraft,
    SectionPlan,
    StaticCheckReport,
)
from src.services.qa.gate import (
    check_bounds,
    check_citation_markers,
    check_citation_resolves,
    check_numeric_grounded,
    check_renderable,
    check_structure_complete,
    check_uncited_claims,
    gate_candidates,
    run_section_gate,
    uncited_units,
)


def _chunk(content: str, chunk_id: UUID | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id or uuid4(),
        source_id=uuid4(),
        content=content,
        score=0.9,
    )


def _draft(content: str, cited: list[UUID] | None = None) -> SectionDraft:
    return SectionDraft(section_id=uuid4(), content=content, cited_chunk_ids=cited or [])


# ---------- citation_resolves (HARD) ----------


class TestCitationResolves:
    def test_all_citations_in_pool_passes(self):
        c1, c2 = uuid4(), uuid4()
        draft = _draft("본문", cited=[c1, c2])
        result = check_citation_resolves(draft, {c1, c2, uuid4()})
        assert result.passed is True
        assert result.severity is CheckSeverity.HARD
        assert result.detail is None

    def test_hallucinated_citation_fails(self):
        c1, ghost = uuid4(), uuid4()
        draft = _draft("본문", cited=[c1, ghost])
        result = check_citation_resolves(draft, {c1})
        assert result.passed is False
        assert result.severity is CheckSeverity.HARD
        assert result.detail is not None
        assert "1건" in result.detail

    def test_no_citations_passes(self):
        # 인용이 없으면 미해결 인용도 없다 → 통과 (근거 없음은 numeric_grounded가 잡음).
        result = check_citation_resolves(_draft("본문", cited=[]), {uuid4()})
        assert result.passed is True

    def test_detail_previews_at_most_three(self):
        ghosts = [uuid4() for _ in range(5)]
        result = check_citation_resolves(_draft("본문", cited=ghosts), set())
        assert result.passed is False
        assert "5건" in result.detail
        # 미리보기는 최대 3개만 나열
        assert result.detail.count("-") <= 3 * 4  # UUID당 하이픈 4개


# ---------- renderable (HARD) ----------


class TestRenderable:
    def test_normal_content_passes(self):
        result = check_renderable(_draft("정상적인 본문입니다."))
        assert result.passed is True
        assert result.severity is CheckSeverity.HARD

    def test_empty_content_fails(self):
        result = check_renderable(_draft("   \n  "))
        assert result.passed is False
        assert "비어" in result.detail

    def test_control_char_fails(self):
        result = check_renderable(_draft("정상\x00본문"))
        assert result.passed is False
        assert "제어문자" in result.detail

    def test_newline_and_tab_allowed(self):
        # 탭·개행은 렌더 가능한 문자 — 실패하면 안 됨.
        result = check_renderable(_draft("첫 줄\n\t들여쓴 줄"))
        assert result.passed is True


# ---------- numeric_grounded (SOFT) ----------


class TestNumericGrounded:
    def test_number_present_in_evidence_passes(self):
        draft = _draft("매출은 1,234억 원이다.")
        result = check_numeric_grounded(draft, cited_content="자료에 따르면 1,234억 원 규모")
        assert result.passed is True
        assert result.severity is CheckSeverity.SOFT

    def test_fabricated_number_flagged(self):
        draft = _draft("성장률은 42.7%에 달했다.")
        result = check_numeric_grounded(draft, cited_content="성장률은 낮았다.")
        assert result.passed is False
        assert "42.7" in result.detail

    def test_comma_normalization(self):
        # 본문은 콤마 있음, 근거는 콤마 없음 — 정규화 후 매칭돼야.
        draft = _draft("총액 1,000,000")
        result = check_numeric_grounded(draft, cited_content="총액 1000000 확인")
        assert result.passed is True

    def test_single_digits_ignored(self):
        # 한 자리 구조적 숫자(1개·2장)는 검사 대상이 아님 — 근거가 비어도 통과.
        draft = _draft("1개의 사례와 2가지 방법")
        result = check_numeric_grounded(draft, cited_content="")
        assert result.passed is True

    def test_multiple_ungrounded_deduped_in_count(self):
        draft = _draft("42.7% 그리고 다시 42.7% 그리고 별도로 99.9%")
        result = check_numeric_grounded(draft, cited_content="근거 없음")
        # 42.7이 두 번 나와도 1건으로 집계 → 총 2건.
        assert result.passed is False
        assert "2건" in result.detail


# ---------- bounds (SOFT) ----------


class TestBounds:
    def test_within_bounds_passes(self):
        result = check_bounds(_draft("본문 내용"), min_chars=1, max_chars=100)
        assert result.passed is True
        assert result.severity is CheckSeverity.SOFT

    def test_too_short_fails(self):
        result = check_bounds(_draft("짧음"), min_chars=100)
        assert result.passed is False
        assert "너무 짧음" in result.detail

    def test_too_long_fails(self):
        result = check_bounds(_draft("가" * 50), min_chars=1, max_chars=10)
        assert result.passed is False
        assert "너무 김" in result.detail

    def test_placeholder_detected(self):
        result = check_bounds(_draft("본문 {{여기}}"), min_chars=1, max_chars=100)
        assert result.passed is False
        assert "placeholder" in result.detail

    def test_forbidden_term_detected(self):
        result = check_bounds(
            _draft("이것은 기밀 문서"),
            min_chars=1,
            max_chars=100,
            forbidden_terms=["기밀"],
        )
        assert result.passed is False
        assert "금칙어" in result.detail

    def test_multiple_problems_joined(self):
        result = check_bounds(_draft("TODO"), min_chars=100, max_chars=1000)
        # 너무 짧음 + placeholder 둘 다.
        assert result.passed is False
        assert ";" in result.detail


# ---------- StaticCheckReport (excluded / warnings) ----------


class TestStaticCheckReport:
    def test_hard_failure_excludes(self):
        report = StaticCheckReport(
            results=[
                GateResult(check="a", severity=CheckSeverity.HARD, passed=False),
                GateResult(check="b", severity=CheckSeverity.SOFT, passed=True),
            ]
        )
        assert report.excluded is True

    def test_only_soft_failure_not_excluded(self):
        report = StaticCheckReport(
            results=[
                GateResult(check="a", severity=CheckSeverity.HARD, passed=True),
                GateResult(check="b", severity=CheckSeverity.SOFT, passed=False, detail="경고"),
            ]
        )
        assert report.excluded is False
        assert len(report.warnings) == 1
        assert report.warnings[0].check == "b"

    def test_all_pass_no_warnings(self):
        report = StaticCheckReport(
            results=[GateResult(check="a", severity=CheckSeverity.HARD, passed=True)]
        )
        assert report.excluded is False
        assert report.warnings == []


# ---------- run_section_gate (검사 통합) ----------


class TestRunSectionGate:
    def test_clean_draft_not_excluded(self):
        chunk = _chunk("근거 본문에 42.7% 라는 수치가 있다.")
        draft = SectionDraft(
            section_id=uuid4(),
            content="분석 결과 42.7% 를 확인했다. " * 20,
            cited_chunk_ids=[chunk.chunk_id],
        )
        report = run_section_gate(draft, [chunk], min_chars=10)
        assert len(report.results) == 6  # citation_markers(2026-08-05)·uncited_claims(2026-08-11)
        assert report.excluded is False

    def test_hallucinated_citation_excludes(self):
        chunk = _chunk("근거", chunk_id=uuid4())
        draft = SectionDraft(
            section_id=uuid4(),
            content="본문 내용 " * 20,
            cited_chunk_ids=[uuid4()],  # 풀에 없는 인용
        )
        report = run_section_gate(draft, [chunk], min_chars=10)
        assert report.excluded is True

    def test_only_cited_chunks_ground_numbers(self):
        # 숫자는 '인용한' 청크에서만 근거를 찾는다 — 인용 안 한 청크에 있어도 미근거.
        cited = _chunk("일반 서술")
        uncited = _chunk("매출 88억")
        draft = SectionDraft(
            section_id=uuid4(),
            content="매출은 88억 원이다. " * 20,
            cited_chunk_ids=[cited.chunk_id],
        )
        report = run_section_gate(draft, [cited, uncited], min_chars=10)
        numeric = next(r for r in report.results if r.check == "numeric_grounded")
        assert numeric.passed is False  # 인용 안 한 청크의 근거는 무효


# ---------- gate_candidates (survivors 필터링) ----------


class TestGateCandidates:
    def test_survivors_exclude_hard_failures(self):
        chunk = _chunk("근거 본문")
        section_id = uuid4()
        good = SectionDraft(
            section_id=section_id,
            content="충분히 긴 본문입니다. " * 20,
            cited_chunk_ids=[chunk.chunk_id],
        )
        bad = SectionDraft(
            section_id=section_id,
            content="유령 인용 본문 " * 20,
            cited_chunk_ids=[uuid4()],  # HARD 실패
        )
        result = gate_candidates(section_id, [good, bad], [chunk], min_chars=10)
        assert len(result.candidates) == 2
        assert len(result.survivors) == 1
        assert result.survivors[0].draft is good

    def test_section_id_propagates(self):
        section_id = uuid4()
        result = gate_candidates(section_id, [], [], min_chars=1)
        assert result.section_id == section_id
        assert result.candidates == []


# ---------- structure_complete (보고서 레벨, HARD) ----------


class TestStructureComplete:
    def test_all_sections_present_passes(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        selected = [
            _draft("a").model_copy(update={"section_id": s1.section_id}),
            _draft("b").model_copy(update={"section_id": s2.section_id}),
        ]
        result = check_structure_complete(selected, [s1, s2])
        assert result.passed is True
        assert result.severity is CheckSeverity.HARD

    def test_missing_section_fails(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        selected = [_draft("a").model_copy(update={"section_id": s1.section_id})]
        result = check_structure_complete(selected, [s1, s2])
        assert result.passed is False
        assert "누락 섹션 1개" in result.detail


class TestCitationMarkers:
    """비표준 인용 마커 검출 — '[배경자료 제공됨]'류 오염 신호(2026-08-05 숏폼 실측)."""

    def test_standard_numeric_citations_pass(self):
        result = check_citation_markers(_draft("고령화율은 24.1%임 [1]. 상승세임 [12]."))
        assert result.passed is True

    def test_invented_background_markers_flagged(self):
        content = "이용률은 70.7%에 달함 [배경자료 제공됨]. 성장 전망임 [배경 맥락]."
        result = check_citation_markers(_draft(content))
        assert result.passed is False
        assert "[배경자료 제공됨]" in result.detail
        assert result.severity.value == "soft"  # 경고 — 후보 제외는 아님

    def test_markdown_links_and_captions_allowed(self):
        content = "[출처 링크](https://ex.com) 참고, [그림 1-2] 및 [표 3] 기준 [2]"
        result = check_citation_markers(_draft(content))
        assert result.passed is True

    def test_duplicate_markers_counted_once(self):
        content = "A [배경 맥락] B [배경 맥락] C [배경 맥락]"
        result = check_citation_markers(_draft(content))
        assert "1종" in result.detail


# ---------- 무근거 주장 (2026-08-11) ----------


class TestUncitedClaims:
    """마커가 가리키는 근거와의 불일치는 numeric_grounded가 잡지만, 아예 마커가
    없는 문장은 어떤 검사에도 안 걸렸다. 근거 없이 쓴 대목이 여기서 드러난다."""

    def test_headings_and_short_items_not_counted(self):
        content = "## 소제목\n\n| 표 | 행 |\n\nㅇ 요약\n\n---\n"
        assert uncited_units(content) == []

    def test_cited_sentence_not_counted(self):
        content = "국내 시장은 전년 대비 12% 성장한 것으로 나타났다 [3]."
        assert uncited_units(content) == []

    def test_uncited_sentence_counted(self):
        content = "국내 시장은 전년 대비 12% 성장한 것으로 나타났다."
        assert len(uncited_units(content)) == 1

    def test_bullet_marker_stripped_before_length_check(self):
        content = "- 반도체 수요는 2027년까지 계속 늘어날 전망이다."
        assert len(uncited_units(content)) == 1

    def test_mostly_cited_draft_passes(self):
        content = (
            "첫 주장이다 [1]. 둘째 주장이다 [2]. 셋째 주장이다 [3]. "
            "근거 없는 짧은 보충 설명 문장이다."
        )
        result = check_uncited_claims(_draft(content))
        assert result.passed is True
        assert result.severity == CheckSeverity.SOFT

    def test_mostly_uncited_draft_warns(self):
        content = (
            "국내 반도체 시장은 전년 대비 크게 성장한 것으로 파악된다. "
            "특히 차량용 부문은 공급 부족이 이어질 전망으로 분석된다. "
            "정부도 이에 맞춰 지원 예산을 확대할 필요가 있다고 판단된다."
        )
        result = check_uncited_claims(_draft(content))
        assert result.passed is False
        assert "3건" in (result.detail or "")
