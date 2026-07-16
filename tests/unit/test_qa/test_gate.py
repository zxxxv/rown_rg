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
    check_citation_resolves,
    check_numeric_grounded,
    check_renderable,
    check_structure_complete,
    gate_candidates,
    run_section_gate,
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
        assert len(report.results) == 4
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
