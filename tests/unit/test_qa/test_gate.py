"""정적 게이트 검사의 결정성 검증 — 순수 함수라 DB·LLM 없이 완결."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.core.types import (
    CheckSeverity,
    GateResult,
    RetrievedChunk,
    SectionDraft,
    SectionPlan,
    StaticCheckReport,
)
from src.services.qa.gate import (
    arithmetic_suspects,
    check_bounds,
    check_citation_attribution,
    check_citation_markers,
    check_citation_resolves,
    check_complete,
    check_leftovers,
    check_numeric_grounded,
    check_renderable,
    check_structure_complete,
    check_uncited_claims,
    claim_coverage,
    claim_units,
    claim_years,
    gate_candidates,
    korean_magnitude,
    leftover_artifacts,
    misattributed_numbers,
    normalize_haystack,
    number_in_text,
    number_variants,
    numeric_mentions,
    run_section_gate,
    significant_numbers,
    truncated_lines,
    uncited_units,
    uncovered_units,
    ungrounded_numbers,
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


# ---------- complete (HARD) ----------


class TestComplete:
    """max_tokens 컷·refusal로 문장 중간에 끊긴 토막이 '완성 절'로 채택된 실사고
    (2026-08-13, 7.1절 216자) 재발 방지 — 미완결 초안은 HARD 제외."""

    def test_complete_draft_passes(self):
        result = check_complete(_draft("정상 완결된 본문."))
        assert result.passed is True
        assert result.severity is CheckSeverity.HARD

    def test_truncated_draft_fails_hard(self):
        draft = _draft("문장 중간에 끊긴 (출처 ").model_copy(
            update={"incomplete_reason": "max_tokens"}
        )
        result = check_complete(draft)
        assert result.passed is False
        assert result.severity is CheckSeverity.HARD
        assert "max_tokens" in result.detail

    def test_truncated_candidate_excluded_by_gate(self):
        # run_section_gate 종합에서도 HARD로 후보가 제외돼야 재생성 경로가 돈다.
        chunk = _chunk("근거")
        draft = _draft("끊긴 본문 " * 30, cited=[chunk.chunk_id]).model_copy(
            update={"incomplete_reason": "refusal"}
        )
        report = run_section_gate(draft, [chunk], min_chars=1, max_chars=10_000)
        assert report.excluded is True


# ---------- numeric_grounded (SOFT) ----------


class TestNumericGrounded:
    def test_number_present_in_evidence_passes(self):
        draft = _draft("지난해 매출은 1,234억 원으로 집계된 것으로 나타났다.")
        result = check_numeric_grounded(draft, cited_content="자료에 따르면 1,234억 원 규모")
        assert result.passed is True
        assert result.severity is CheckSeverity.SOFT

    def test_fabricated_number_flagged(self):
        draft = _draft("올해 성장률은 42.7%에 달한 것으로 집계됐다.")
        result = check_numeric_grounded(draft, cited_content="성장률은 낮았다.")
        assert result.passed is False
        assert "42.7" in result.detail

    def test_comma_normalization(self):
        # 본문은 콤마 있음, 근거는 콤마 없음 — 정규화 후 매칭돼야.
        draft = _draft("사업 총액은 1,000,000원 규모로 편성된 것으로 확인됐다.")
        result = check_numeric_grounded(draft, cited_content="총액 1000000 확인")
        assert result.passed is True

    def test_single_digits_ignored(self):
        # 한 자리 구조적 숫자(1개·2장)는 검사 대상이 아님 — 근거가 비어도 통과.
        draft = _draft("본 조사는 1개의 사례와 2가지 방법을 대상으로 수행됐다.")
        result = check_numeric_grounded(draft, cited_content="")
        assert result.passed is True

    def test_multiple_ungrounded_deduped_in_count(self):
        draft = _draft("성장률은 42.7%였고 다시 42.7%로 유지됐으며 점유율은 99.9%에 달했다.")
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
        # citation_markers(2026-08-05)·uncited_claims(2026-08-11)·complete(2026-08-13)
        # ·leftovers·citation_attribution(2026-08-14)
        assert len(report.results) == 9
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
            content="지난해 매출은 88억 원으로 집계된 것으로 나타났다. " * 20,
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
        assert "미작성 절 1개" in result.detail
        assert "2.1" in result.detail  # 어느 절인지 사람이 바로 알 수 있어야 한다

    def test_empty_draft_counts_as_missing(self):
        # 0자 절이 '선택됨'만으로 완성 취급된 실사고(2026-08-13, 6.1절) 재발 방지 —
        # 내용 없는 초안은 누락과 동일하게 실패해야 한다.
        s1 = SectionPlan(chapter_number=6, section_number=1, title="사업화 가능성")
        selected = [_draft("   \n").model_copy(update={"section_id": s1.section_id})]
        result = check_structure_complete(selected, [s1])
        assert result.passed is False
        assert "6.1" in result.detail


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


def test_인용_마커의_출처_번호는_수치가_아니다() -> None:
    """실측 오탐(2026-08-12 검증 런 화면): "(출처 31)"의 31이 '근거에서 확인되지 않는
    수치'로 떴다. 출처 번호는 주장이 아니고, 마커가 많은 절일수록 경고가 늘어 진짜
    신호를 덮는다."""
    body = "숏폼이 플랫폼 체류 시간의 약 46%를 차지하고 있음 (출처 31)"
    assert ungrounded_numbers(body, "릴스는 체류 시간의 46%를 차지한다") == []
    # 마커를 걷어내도 본문의 진짜 수치는 그대로 잡는다.
    assert ungrounded_numbers(body, "관련 내용 없음") == ["46%"]


def test_대괄호_직접인용_마커도_걷어낸다() -> None:
    body = "글로벌 시장은 2026년까지 1,350억 달러로 성장할 전망임[12]"
    assert ungrounded_numbers(body, "1350억 달러 규모로 성장") == []


def test_연월_표기는_수치가_아니다() -> None:
    """실측 오탐(2026-08-14 탄소규제 런): critical 26건 중 표본 22건이 오탐, 다수가
    "’25.10"(2025년 10월)·"2019.12"류 연월 표기였다. 날짜는 수치 주장이 아니다."""
    body = "개정 규정은 2019.12 발표 후 ’25.10 개정을 거쳐 ’24년부터 단계 적용되고 있음 (출처 3)"
    assert ungrounded_numbers(body, "관련 제도 연혁 서술") == []
    # 아포스트로피 없는 진짜 소수·퍼센트는 그대로 잡는다.
    body2 = "시장 점유율이 22.3%까지 상승한 것으로 조사됐음 (출처 3)"
    assert ungrounded_numbers(body2, "관련 내용 없음") == ["22.3%"]


class TestLeftoversAndTruncation:
    def test_editing_memo_and_part_terms_flagged(self):
        content = (
            "ㅇ 관련 제도의 활용이 저조함(출처 15의 내용 대신 확인 가능한 범위로 한정 — 삭제)\n"
            "본 파트에서는 국내 동향을 정리한다.\n# \n"
        )
        found = leftover_artifacts(content)
        assert any("삭제" in f for f in found)
        assert any("본 파트" in f for f in found)
        assert any("헤딩" in f for f in found)
        result = check_leftovers(_draft(content))
        assert result.passed is False and result.severity is CheckSeverity.SOFT

    def test_clean_content_passes(self):
        content = "ㅇ 국내 기업의 대응 수준은 개선되는 흐름을 보이고 있음 (출처 3)"
        assert leftover_artifacts(content) == []
        assert truncated_lines(content) == []
        assert check_leftovers(_draft(content)).passed is True

    def test_new_leftover_forms_flagged(self):
        # 2026-08-15 검증런 신형: 배정 메모·오염 마커·기형 callout - 세정(scrub)과
        # 같은 패턴 계열로 게이트도 가시화한다(세정 밖 경로 대비).
        found = leftover_artifacts(
            "ㅇ 추정치가 제시됨 (출처 17 제외)\n"
            "ㅇ 평가가 이어짐 (출처 21은 사용 불가 — 해당 서술 생략)\n"
            "ㅇ 부담이 확대됨 (출превод처 25)\n"
            "<callout(warn)>\n고지\n</callout>"
        )
        joined = " ".join(found)
        assert "배정 메모" in joined
        assert "오염된 출처 마커" in joined
        assert "callout" in joined

    def test_legit_prose_not_flagged_as_memo(self):
        content = (
            "ㅇ 판재류는 대상에서 제외된 품목임 (출처 22). 상세는 (출처 12에서 제외된 품목) 참조"
        )
        assert leftover_artifacts(content) == []

    def test_mid_section_truncation_detected(self):
        # 실측(2026-08-14 탄소규제 런 4.4): 파트 결합부에서 "…GDP가 약 2"로 끊긴 채
        # 다음 항목으로 넘어갔다 - 절 끝 검사로는 안 잡히는 위치다.
        content = (
            "ㅇ 1.5℃ 시나리오 이행 시 탄소규제 미대응 기업의 ’50년까지 GDP가 약 2\n"
            "ㅇ 다음 항목은 정상적으로 이어지는 서술임 (출처 5)"
        )
        cut = truncated_lines(content)
        assert len(cut) == 1 and "약 2" in cut[0]

    def test_latin_terms_ending_with_digits_not_flagged(self):
        # RE100·CN 7204처럼 숫자로 끝나는 용어·코드는 절단이 아니다 - 정밀도 우선.
        content = (
            "ㅇ 국내 기업의 상당수가 이행 수단으로 검토하는 제도는 RE100\n"
            "ㅇ 철강 판재류의 관세분류 체계상 핵심 품목 코드는 CN 7204\n"
            "| 지표 | 값 3 |\n"
        )
        assert truncated_lines(content) == []


class TestClaimCoverage:
    """커버리지는 검출 지표의 분모다 - claim_units가 못 집은 문장은 모든 검사에서
    증발하는데 그 손실은 정밀도·재현율 어디에도 안 나타난다('남' 실사고, 2026-08-14).
    """

    def test_josa_nominal_endings_picked(self):
        # 코퍼스 실측 꼬리들 - 수치를 실은 개조식 명사 종결은 전부 주장으로 집혀야 한다.
        endings = [
            "ㅇ 국내 참여 기업의 복수 수단 병행 비율은 중견기업 1.1%에 그침",
            "ㅇ 한국의 유효탄소가격은 29.9EUR/tCO2로 미국의 약 2.5배 수준",
            "ㅇ 대응 수준은 자가진단 문항 12개로 구성된 자기평가 척도로 측정",
            "ㅇ 수출 실적 100만 달러 이상 제조기업을 핵심 모집단으로 설정",
            "ㅇ 재생에너지 조달 시장은 연 4,500억원 안팎의 규모를 형성",
        ]
        for line in endings:
            assert claim_units(line) != [], line

    def test_trailing_paren_stripped_before_tail_check(self):
        line = "ㅇ 자가발전 선호가 규모와 무관하게 가장 높게 나타남(복수응답)"
        assert claim_units(line) != []

    def test_caption_and_unit_lines_not_candidates(self):
        content = (
            "표: 국내 제조 수출기업의 기업 규모별 RE100 이행수단 이용 현황\n"
            "그림: 연도별 참여 기업 수 추이(2020~2023)\n"
            "(단위: %, ’23년 기준 - 무응답 제외 표본 610개사)\n"
        )
        picked, total, missed = claim_coverage(content)
        assert total == 0 and picked == 0 and missed == []

    def test_coverage_counts_and_missed_numeric(self):
        content = (
            "ㅇ 참여 기업 수는 전년 대비 21개사 증가한 것으로 집계됐음\n"  # 픽업
            "ㅇ 향후 정책 방향에 대한 종합적 검토와 제도 개선 논의\n"  # 명사 종결·무수치 - 미픽업
        )
        picked, total, missed = claim_coverage(content)
        assert (picked, total) == (1, 2)
        assert missed == []  # 수치=주장 규칙이 있는 한 미포착 수치는 구조적으로 0

    def test_uncovered_units_is_the_complement_of_claim_units(self):
        """화면이 짚는 "대조 안 함" 목록은 커버리지 숫자와 같은 판정을 써야 한다.

        둘이 갈리면 "후보 2개 중 1개 픽업"이라 해 놓고 목록에는 엉뚱한 줄이 뜬다 -
        사람은 어느 쪽이 거짓말인지 알 수가 없다(2026-08-26).
        """
        content = (
            "ㅇ 참여 기업 수는 전년 대비 21개사 증가한 것으로 집계됐음\n"  # 픽업
            "ㅇ 향후 정책 방향에 대한 종합적 검토와 제도 개선 논의\n"  # 미픽업
        )
        picked, total, _ = claim_coverage(content)
        uncovered = uncovered_units(content)
        assert len(uncovered) == total - picked == 1
        assert "제도 개선 논의" in uncovered[0]
        # 픽업된 줄이 목록에 섞이면 안 된다 - 밑줄이 두 겹으로 그어진다.
        assert not set(uncovered) & set(claim_units(content))

    def test_captions_are_not_listed_as_uncovered(self):
        # 후보 자체가 아닌 줄(캡션·단위)은 "대조 안 함"도 아니다 - 안 그러면 표·그림
        # 제목마다 회색 밑줄이 그어져 목록이 소음이 된다.
        assert uncovered_units("표: 기업 규모별 이행수단 이용 현황\n(단위: %, ’23년 기준)\n") == []

    def test_numeric_caption_is_reported_as_missed(self):
        # 캡션 제외가 새 맹점이 되면 안 된다 - 수치 주장을 캡션 꼴로 쓴 줄은
        # missed_numeric으로 발화한다(동어반복 지표 방지, 2026-08-14 지침).
        _, _, missed = claim_coverage("표 3: 2030년 국가 감축목표 40% 달성 경로")
        assert len(missed) == 1 and "40%" in missed[0]
        # 용어 숫자(RE100)·연도만 담은 통상 캡션은 무해하다.
        assert claim_coverage("표: 글로벌 RE100 가입·목표 설정·보고 기준(’23년)")[2] == []

    def test_term_digits_and_year_suffix_not_significant(self):
        # RE100·B2B의 숫자는 식별자 조각, 10년차·30년간은 기간 표기 - 수치 주장이 아니다
        # (실측: 캡션 오탐 37건 중 36건이 RE100의 100).
        body = "RE100 대응 기업은 지난 10년간 B2B 계약을 확대해 온 것으로 나타났음 (출처 3)"
        assert ungrounded_numbers(body, "관련 서술") == []
        # 라틴 접두가 아닌 진짜 수치는 그대로 잡는다("60TWh"는 숫자가 앞).
        body2 = "국내 기업이 공시한 연간 전력사용량 합계는 60TWh 규모로 집계됐음 (출처 3)"
        assert ungrounded_numbers(body2, "관련 없음") == ["60"]

    def test_claim_years_extracts_explicit_years_only(self):
        # 수치 검사가 버리는 연도를 주입 가드가 되집는다 - RE100의 100은 연도가 아니고,
        # 더 긴 수의 조각(52024)이나 소수 꼬리(3.2024)도 아니다.
        assert claim_years("2024년 기준 428개사이며 2019.12부터 시행됨 [1]") == ("2024", "2019")
        assert claim_years("RE100 목표는 성장률 3.2024나 계약액 52024와 무관함") == ()
        assert claim_years("연도 없는 수치 428개사 서술") == ()


class TestCrossLingualNumbers:
    """한↔영 자릿수 환산 — 영문 코퍼스에서 수치 검출기가 통째로 무력했던 원인.

    2026-08-24 COMPA 실측(정밀도 14.8%): 본문 "70.5억 달러"와 근거 "USD 7.05
    billion"이 콤마 제거 부분문자열로는 영영 못 만나 전부 '무근거'로 떨어졌다.
    """

    def test_korean_magnitude_value(self):
        assert korean_magnitude("70.5억") == 7.05e9
        assert korean_magnitude("4,610만") == 4.61e7
        assert korean_magnitude("2억 450만") == 2.045e8
        assert korean_magnitude("42.7%") is None

    def test_variants_include_english_mantissa(self):
        assert "7.05" in number_variants("70.5억")  # USD 7.05 billion
        assert "46.1" in number_variants("4,610만")  # US$ 46.1 million
        assert "204.5" in number_variants("2억450만")  # US$ 204.5 million

    def test_round_magnitude_needs_scale_word(self):
        """딱 떨어지는 큰 수의 가수는 한 자리라 아무 글에나 붙었다 (2026-08-27 실측).

        "10억 달러" 주장이 영문 그림 캡션 "Fig. 31 Others market, 2018 - 2030
        (USD Million)"에 근거 있음으로 판정됐다 - 가수 "1"이 "31"에도 "2018"에도
        들어 있어서다. 코퍼스 215절에서 이렇게 거짓 통과한 수치가 106건이었고,
        무근거 경고가 안 뜨니 화면에는 아무 표시도 없었다.
        """
        assert "1" not in number_variants("10억")
        assert "10" not in number_variants("100억")
        noise = normalize_haystack("Fig. 31 Others market, 2018 - 2030 (USD Million)")
        assert not number_in_text("10억", noise)
        assert not number_in_text("1조", normalize_haystack("1 knowledge base entry in 2019"))

    def test_number_needs_digit_boundary(self):
        """맨 부분문자열 대조는 수를 토막으로 만난다 (2026-08-27 실측).

        주장의 "10"이 근거 URL의 **RE100** 안에 걸렸고, "4,610만"의 가수 46.1이
        "46.15 million"에, "1,623억"의 162.3이 "2162.3"에 걸렸다. 앞뒤에 숫자가
        붙으면 다른 수다. 코퍼스 207절에서 이렇게 거짓 통과한 수치가 148건이었다.

        소수 뒤의 0은 같은 수라 받는다 - "46.1"과 "46.10"을 가르면 진짜 근거를 잃는다.
        """
        assert not number_in_text("10", normalize_haystack("the global RE100 initiative"))
        assert not number_in_text("4,610만", normalize_haystack("46.15 million units"))
        assert not number_in_text("1,623억", normalize_haystack("2162.3"))
        assert not number_in_text("70.5억", normalize_haystack("USD 170.5 billion"))
        assert number_in_text("4,610만", normalize_haystack("46.10 million"))
        assert number_in_text("1,623억", normalize_haystack("162.3 billion by 2030"))
        assert number_in_text("2,750만", normalize_haystack("2,750만 달러에서"))

    def test_round_magnitude_still_matches_english_scale(self):
        """낱말이 붙으면 인정한다 - 환산 회수 능력은 그대로 둔다."""
        for hay in ("approach USD 1 billion by 2030", "US$1.0 billion", "1 trillion won"):
            token = "1조" if "trillion" in hay else "10억"
            assert number_in_text(token, normalize_haystack(hay)), hay
        # 한글 표기는 종전대로 맨 문자열로 잡힌다.
        assert number_in_text("10억", normalize_haystack("매출은 10억 달러에 달했다"))

    def test_english_evidence_grounds_korean_claim(self):
        body = "ㅇ 글로벌 액체생검 시장은 2025년 70.5억 달러 규모로 평가된 것으로 나타났음 (출처 4)"
        evidence = "The global liquid biopsy market was valued at USD 7.05 billion in 2025."
        # 무관한 근거에는 잡히고(주장 단위가 실제로 잡힌다는 증거), 환산 근거에는 안 잡힌다.
        assert ungrounded_numbers(body, "관련 없는 근거 문장") == ["70.5억"]
        assert ungrounded_numbers(body, evidence) == []

    def test_compound_magnitude_not_split(self):
        # "2억 450만"을 쪼개 읽으면 450이 홀로 남아 엉뚱한 자료에 붙는다(주입 의심 오탐).
        body = "ㅇ cfRNA 진단 세부 영역은 2035년 2억 450만 달러 규모로 전망되고 있음 (출처 3)"
        assert "450" not in numeric_mentions(body)
        assert ungrounded_numbers(body, "관련 없는 근거 문장") == ["2억 450만"]
        assert ungrounded_numbers(body, "projected to reach US$ 204.5 million by 2035") == []

    def test_real_gap_still_flagged(self):
        # 환산으로도 안 맞는 진짜 창작은 그대로 잡힌다.
        body = "ㅇ 글로벌 액체생검 시장은 2025년 88.8억 달러 규모로 평가된 것으로 나타났음 (출처 4)"
        evidence = "The global liquid biopsy market was valued at USD 7.05 billion in 2025."
        assert ungrounded_numbers(body, evidence) == ["88.8억"]


class TestSectionRefsAndDerived:
    """절 번호와 파생치는 근거 대조 대상이 아니다(2026-08-24 COMPA 오탐 2종)."""

    def test_section_reference_is_not_a_number(self):
        body = "ㅇ 진단 범위 확장에 따른 시장 규모 확대 전망은 1.1절을 참조하기 바람 (출처 14)"
        # 절 번호는 문서 안 길찾기지 수치 주장이 아니다 — 근거가 무관해도 잡히면 안 된다.
        assert ungrounded_numbers(body, "관련 없는 근거 문장") == []

    def test_derived_ratio_skipped(self):
        # 24.12 ÷ 6.16 = 3.916 ≈ 3.9배 — 근거엔 24.12와 6.16만 있다.
        body = (
            "ㅇ AI 암 진단의 CAGR 24.12%는 암 진단 전체 시장 성장률 6.16% 대비"
            " 약 3.9배 수준으로 나타났음 (출처 14)"
        )
        evidence = "AI in cancer diagnostics CAGR 24.12% vs overall market 6.16%"
        assert ungrounded_numbers(body, evidence) == []

    def test_unrelated_number_not_treated_as_derived(self):
        body = "ㅇ 참여 기업 비중은 24.12%와 6.16%이며 별도 지표는 77.7%로 집계됐음 (출처 1)"
        evidence = "shares were 24.12% and 6.16% respectively"
        assert ungrounded_numbers(body, evidence) == ["77.7%"]


class TestBibliographicIdentifiers:
    """문헌을 가리키는 숫자는 수치 주장이 아니다(2026-08-27 실측: 무근거 경고
    표본 8건 중 3건이 과제번호·권호였다 - 경고가 헛돌면 진짜가 묻힌다).

    코퍼스 220절 재측정: 경고 23건 소거·신규 0건, 소거분 전수가 식별자·모델명
    (COVID-19, MI-100, PAM-16, 출원번호, 권·호)이었다."""

    def test_hyphenated_identifier_fragments_excluded(self):
        # 과제·출원 번호의 토막 - 하이픈으로 앞 글자·숫자에 붙은 숫자.
        body = "ㅇ 'AI 반도체 기술 개발(No.RS-2025-02263167)' 과제가 성과로 이어짐 (출처 25)"
        assert ungrounded_numbers(body, "관련 없는 근거") == []
        body2 = "ㅇ COVID-19 이후 원격근무 채택이 가속화되었음 (출처 3)"
        assert ungrounded_numbers(body2, "관련 없는 근거") == []

    def test_volume_issue_excluded(self):
        body = "ㅇ 해당 서베이는 IEEE 회보 105권 12호에 게재되어 학술 기반을 형성함 (출처 30)"
        assert ungrounded_numbers(body, "관련 없는 근거") == []

    def test_leading_zero_is_identifier(self):
        # 수량은 0으로 시작해 적지 않는다 - 소수(0.3%)는 수치다.
        assert significant_numbers("출원번호 0085973에 따른 특허") == []
        assert significant_numbers("비중은 0.3% 수준") == ["0.3%"]

    def test_real_quantities_still_checked(self):
        # 같은 문장 안이라도 진짜 수량은 남는다 - "512 Gbps"·범위 뒤쪽 %.
        got = significant_numbers("2037년 512 Gbps PAM-16 방식, 점유율 10-20% 수준")
        assert "512" in got and "20%" in got and "16" not in got


class TestMisattributedNumbers:
    """마커 오귀속 - 프로브가 공짜로 실증한 구멍(합성 오귀속 5건을 판정 축 전원 통과,
    0/5)의 결정적 마감. 유령 출처 검사는 '존재하는 번호로 잘못 가리키기'를 못 본다."""

    def _pool(self):
        a, b = uuid4(), uuid4()
        return (
            a,
            b,
            {
                a: "국내 생산 능력은 42.7% 확대된 것으로 조사됐다.",
                b: "정부는 관련 제도 정비를 추진하고 있다.",
            },
        )

    def test_wrong_marker_flagged(self):
        # 수치의 실제 출처는 A인데 마커는 B를 가리킨다 - 프로브 5건의 형태.
        a, b, pool = self._pool()
        content = "ㅇ 국내 생산 능력은 42.7% 확대된 것으로 나타났음 (출처 2)"
        found = misattributed_numbers(content, {2: [b]}, pool)
        assert len(found) == 1 and "42.7" in found[0]

    def test_correct_marker_clean(self):
        a, b, pool = self._pool()
        content = "ㅇ 국내 생산 능력은 42.7% 확대된 것으로 나타났음 (출처 1)"
        assert misattributed_numbers(content, {1: [a]}, pool) == []

    def test_number_nowhere_is_not_misattribution(self):
        # 풀 어디에도 없는 수치는 무근거(다른 검사 몫)지 오귀속이 아니다.
        a, b, pool = self._pool()
        content = "ㅇ 국내 생산 능력은 99.9% 확대된 것으로 나타났음 (출처 2)"
        assert misattributed_numbers(content, {2: [b]}, pool) == []

    def test_english_cited_chunk_skipped(self):
        # 인용 근거가 외국어면 어휘로 '없다'를 선언할 수 없다(단위 환산) - 건너뛴다.
        a = uuid4()
        b = uuid4()
        pool = {a: "연간 세수는 72억 달러 규모로 추산된다.", b: "Revenue reaches $7.2 billion."}
        content = "ㅇ 연간 세수는 72억 달러 규모로 추산됨 (출처 2)"
        assert misattributed_numbers(content, {2: [b]}, pool) == []

    def test_gate_check_uses_cited_order_contract(self):
        # 로컬 번호 첫 등장 순서 = cited_chunk_ids 순서 규약으로 매핑을 편다.
        a, b, pool = self._pool()
        chunks = [_chunk(pool[a], a), _chunk(pool[b], b)]
        draft = _draft("ㅇ 생산 능력은 42.7% 확대된 것으로 나타났음 (출처 1)", cited=[b])
        result = check_citation_attribution(draft, chunks)
        assert result.passed is False and "42.7" in (result.detail or "")
        draft_ok = _draft("ㅇ 생산 능력은 42.7% 확대된 것으로 나타났음 (출처 1)", cited=[a])
        assert check_citation_attribution(draft_ok, chunks).passed is True


class TestArithmeticSuspects:
    def test_wrong_growth_rate_flagged(self):
        # 실측 결함(2026-08-14 탄소규제 런 1.2): 240→289는 +20.4%인데 30%로 서술.
        content = "ㅇ 참여기업의 재생에너지 사용량은 전년 240TWh에서 289TWh로 30% 증가함 (출처 28)"
        found = arithmetic_suspects(content)
        assert len(found) == 1 and "240" in found[0] and "30%" in found[0]

    def test_correct_growth_rate_passes(self):
        content = (
            "ㅇ 참여기업의 재생에너지 사용량은 전년 240TWh에서 289TWh로 20.4% 증가함 (출처 28)"
        )
        assert arithmetic_suspects(content) == []

    def test_percent_point_change_not_judged(self):
        # %p는 상대 증가율 산식이 아니다 - 판정 대상에서 자동 제외돼야 한다.
        content = "ㅇ 신재생에너지 공급 의무 비율이 9%에서 12.5%로 3.5%p 상향 조정됨 (출처 33)"
        assert arithmetic_suspects(content) == []

    def test_correct_sum_passes_wrong_sum_flagged(self):
        ok = "ㅇ 정보 제공(30.6%)과 교육(18.1%)을 합한 48.7%가 정보·역량 지원 수요임 (출처 34)"
        assert arithmetic_suspects(ok) == []
        bad = "ㅇ 정보 제공(30.6%)과 교육(18.1%)을 합한 52.7%가 정보·역량 지원 수요임 (출처 34)"
        found = arithmetic_suspects(bad)
        assert len(found) == 1 and "합산 불일치" in found[0]

    def test_three_operand_sum_passes(self):
        content = (
            "ㅇ 사업장 이전(7.5%)·거래처 물색(13.0%)·거래 중단(1.8%)을 합한 22.3%는 미대응군임"
        )
        assert arithmetic_suspects(content) == []

    def test_sum_without_result_number_not_judged(self):
        # 피연산자와 결과가 한 문장에 함께 없으면 판정하지 않는다(정밀도 우선).
        content = "ㅇ 두 항목을 합한 비중이 절반에 가까운 것으로 조사됨 (출처 34)"
        assert arithmetic_suspects(content) == []


class TestNominalMEnding:
    """-ㅁ 명사형 종결은 목록이 아니라 규칙으로 판정한다.

    _CLAIM_TAILS가 음·임·함·됨·짐·옴·남·듦을 낱개로 열거했는데, -ㅁ은 어간 모음에 따라
    무한히 갈린다(나뉘다→나뉨, 갖추다→갖춤). 그래서 "…으로 나뉨"처럼 평범한 개조식
    본문이 근거 대조·무근거 수치·산술 검사에서 통째로 증발했다 —
    2026-08-26 실측: 완료 8개 프로젝트의 미포착 623줄 중 210줄(34%)이 이 계급이었고,
    꼬리 분포는 보여줌 38·큼 19·둠 18·지님 13·갖춤 13으로 거의 전부 동사 명사형이었다.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "ㅇ 검체 축은 혈액·엑소좀·조직으로, 기술 축은 PCR 기반 방법과 NGS로 나뉨",
            "ㅇ 적용 분야를 암·유전질환·감염질환으로 구분해 분야별 시장가치를 전망하는 체계를 갖춤",
            "ㅇ 국내 제조수출기업의 인지·이행 실태 수치는 앞 절에서 확정한 값을 따름",
            "ㅇ EU는 최빈개도국의 녹색전환 지원과 기술지원 제공 의사를 밝힘",
            "ㅇ 업종 협회 차원의 표준 템플릿·대행 창구 제공이 실익이 큼",
            "ㅇ 해당 제도는 공급망 전반에 걸쳐 광범위한 영향을 미침",
        ],
    )
    def test_verb_nominals_are_claims(self, line: str) -> None:
        assert claim_units(line) == [line.removeprefix("ㅇ ")]

    @pytest.mark.parametrize(
        "line",
        [
            # 짧은 명사 소제목은 -ㅁ으로 끝나도 후보 길이(25자)에서 먼저 걸린다.
            "ㅇ 통합 운영 시스템",
            "ㅇ 대응 전략 프로그램",
        ],
    )
    def test_short_noun_headings_still_dropped(self, line: str) -> None:
        assert claim_units(line) == []

    def test_non_hangul_tail_untouched(self) -> None:
        """영문 줄은 여전히 주장이 아니다 - 교차언어 원칙(test_alignment)을 지킨다."""
        assert claim_units("ㅇ The transitional period requires reporting only") == []


class TestShortLinesWithNumbers:
    """25자 컷은 소제목을 막는 장치지만, 짧은 개조식 줄이 바로 수치가 사는 자리다.

    2겹(종결형)을 색칠 경로에서 빼면 길이 컷이 유일한 침묵 장치가 된다 - 임계를
    낮추는 대신 조건부로 연다(2026-08-26): 마커나 유의미 수치가 있으면 짧아도 본다.
    소제목은 보통 둘 다 없고, 절 번호로 시작하는 줄만 따로 막으면 오탐이 남지 않는다.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "ㅇ 액체생검 시장 3.2배 성장",  # 수치만
            "ㅇ 전년 대비 12.1% 증가 [8]",  # 수치 + 마커
            "ㅇ 국내 점유율 41%",
        ],
    )
    def test_short_numeric_lines_are_checked(self, line: str) -> None:
        assert claim_units(line) == [line.removeprefix("ㅇ ")]

    @pytest.mark.parametrize(
        "line",
        [
            "ㅇ 시장 규모 및 전망",  # 수치·마커 없음 = 소제목
            "ㅇ 국내 정책 동향",
            "2.1 기술 현황",  # 절 번호 - 숫자가 있어도 소제목이다
            "3.2 대응 방안 [4]",  # 절 번호가 마커보다 우선한다
        ],
    )
    def test_headings_stay_out(self, line: str) -> None:
        assert claim_units(line) == []


class TestDateNotationSplit:
    """관공서 날짜 표기("2026. 5. 6.")가 문장 분리에 물리던 자리.

    마침표+공백이라 한 문장이 세 조각으로 갈리고, 마커는 마지막 조각에만 붙으므로
    앞 조각들이 "인용 표기 없음"이 됐다 - **멀쩡한 인용 문장이 지어낸 글로 표시된다**.
    게다가 "5."·"6." 조각은 길이 컷에 걸려 본문에서 통째로 증발했다(2026-08-26 지적).
    """

    @pytest.mark.parametrize(
        "line",
        [
            "고시 제2026-15호는 2026. 5. 6. 시행되며 적용 대상 품목을 확대한다 (출처 3).",
            "기준일은 2025. 12. 31.이며 이후 분기별로 갱신된다 [8].",
            "시행일 2026.5.6. 이후 신규 수입분부터 적용된다 (출처 7).",
        ],
    )
    def test_dates_do_not_split_a_sentence(self, line: str) -> None:
        assert claim_units(line) == [line]
        assert uncited_units(line) == []  # 마커가 그 한 문장에 붙어 있다

    @pytest.mark.parametrize(
        ("line", "n"),
        [
            ("전년 대비 12.1% 증가했다. 2026년에는 두 자릿수 성장이 이어질 전망이다.", 2),
            (
                "① 산정경계의 포함범위에서 국내외 차이가 뚜렷하게 확인된다."
                " ② 제3자 검증 의무화 수준에서도 상당한 격차가 확인된다.",
                2,
            ),
        ],
    )
    def test_normal_sentence_breaks_survive(self, line: str, n: int) -> None:
        """날짜만 예외다 - 평범한 문장 경계·원문자 항목은 그대로 쪼개져야 한다."""
        assert len(claim_units(line)) == n


class TestArticleNumbers:
    def test_제N조는_법조문이지_수치가_아니다(self) -> None:
        """v6 실측(2026-08-27): 'CBAM 규정 제21조'의 21조가 21조 원으로 오인돼
        무근거 수치로 남았다 - 절·장·항 번호 제외와 같은 계열."""
        assert numeric_mentions("규정 제21조에 따라 산정된 가격") == []
        assert ungrounded_numbers("규정 제21조에 따라 판매됨 (출처 4)", "무관한 근거") == []
        # 진짜 큰 수는 그대로 - '제'가 없으면 수치 주장이다.
        assert "21조" in numeric_mentions("예산은 21조 원 규모다")
