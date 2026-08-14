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
    arithmetic_suspects,
    check_bounds,
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
    gate_candidates,
    leftover_artifacts,
    run_section_gate,
    truncated_lines,
    uncited_units,
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
        # ·leftovers(2026-08-14)
        assert len(report.results) == 8
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
