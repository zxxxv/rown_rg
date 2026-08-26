"""문장 ↔ 근거 대목 정렬 — 어휘 겹침만으로 청크 안의 줄까지 좁히는 결정적 로직."""

from __future__ import annotations

from uuid import uuid4

from src.services.qa.alignment import (
    ALIGNED_THRESHOLD,
    NO_SCORE,
    align_section,
    overlap_score,
)  # noqa: F401
from src.services.qa.gate import claim_units


class TestOverlapScore:
    def test_verbatim_is_near_one(self):
        s = "민간 기업들은 포항, 광양 등에 정·제련 설비를 투자하며 수직계열화를 시도하고 있음"
        assert overlap_score(s, s) == 1.0

    def test_josa_difference_still_matches(self):
        # 한글은 조사가 붙어 어절이 안 맞는다 - 글자 2-gram이라 넘어가야 한다.
        claim = "공급망의 취약성이 원료 단계에서 발생한다"
        span = "공급망 취약성은 원료 단계에 집중된다"
        assert overlap_score(claim, span) > 0.5  # 조사·어미 차이만큼은 깎인다

    def test_number_dominates(self):
        # 숫자가 맞는 쪽이 이겨야 한다 - 보고서가 지어내는 것도 옮기는 것도 숫자다.
        claim = "시장 규모는 42.7% 증가했다"
        with_number = "관련 시장 규모는 42.7% 늘었다"
        without = "관련 시장 규모는 크게 늘었다"
        assert overlap_score(claim, with_number) > overlap_score(claim, without)

    def test_unrelated_is_low(self):
        assert overlap_score("반도체 수출이 증가했다", "노동 시간 단축 제도의 도입 배경") < 0.2

    def test_long_span_not_penalized(self):
        # 분모는 주장 쪽이다 - 긴 청크에 짧은 근거 한 줄이 정답인 경우가 흔하다.
        claim = "수출이 늘었다"
        short = "수출이 늘었다"
        long = "수출이 늘었다. " + "그밖에 무관한 서술이 길게 이어진다. " * 20
        assert overlap_score(claim, long) == overlap_score(claim, short)

    def test_empty_claim_has_no_score(self):
        """셀 게 없으면 0.0이 아니라 NO_SCORE - 독스트링이 원래 약속하던 계약이다.

        0.0은 "쟀는데 안 겹침"이고 NO_SCORE는 "잴 수가 없음"이라 사람이 할 일이 다르다.
        코드가 0.0을 돌려주는 바람에 미측정이 불일치로 읽혔다(2026-08-26).
        """
        assert overlap_score("[1]", "아무 근거") == NO_SCORE

    def test_collapsed_denominator_has_no_score(self):
        """영문 근거 + 수치 두세 개면 분모가 붕괴해 우연 일치가 1.00이 된다.

        실측(탄소규제 보고서 1,317줄): 0.9 이상 150건 중 118건이 피처 2~3개짜리
        이 계급이었고, 한글 2-gram이 남아 있는 건 3건뿐이었다.
        """
        assert overlap_score("2030년 60% 달성", "Targets of 60% by 2030") == NO_SCORE
        # 한글 대 한글은 짧아도 2-gram이 넉넉해 이 하한에 안 걸린다.
        assert overlap_score("수출이 늘었다", "수출이 늘었다") > 0


class TestClaimUnits:
    def test_marker_ending_sentence_survives(self):
        # 개조식은 "…성장했음 [3]"처럼 마커로 끝난다. 마커를 뗀 뒤 종결형을 봐야 한다
        # (2026-08-11 실측: 이 처리가 없으면 인용 340개짜리 절의 주장이 0건이 됐다).
        content = "ㅇ 국내 이차전지 수출은 전년 대비 크게 증가한 것으로 나타났음 [3]"
        assert len(claim_units(content)) == 1

    def test_noun_ending_heading_dropped(self):
        assert claim_units("공급망 안정성 강화를 위한 핵심광물 확보 전략 제언") == []


class TestAlignSection:
    def test_finds_exact_line_inside_chunk(self):
        cid = uuid4()
        chunk = (
            "## 개요\n"
            "무관한 도입부 문장이 먼저 나온다.\n"
            "- 이미 민간 기업들은 포항, 광양 등에 정·제련 설비를 투자하며 수직계열화 중임.\n"
            "다른 주제의 마무리 문장.\n"
        )
        content = "국내 민간 기업들은 포항, 광양 등에 정·제련 설비를 투자하며 수직계열화 중임 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        assert claim.status == "aligned"
        assert claim.span is not None
        assert "포항, 광양" in claim.span.text
        # 위치는 청크 원문 기준이라 그대로 잘라내면 그 줄이 나와야 한다
        assert chunk[claim.span.start : claim.span.end] == claim.span.text
        assert claim.span.score >= ALIGNED_THRESHOLD

    def test_unrelated_evidence_is_unmatched(self):
        cid = uuid4()
        chunk = "유연근무제 도입 기업의 만족도 조사 결과를 정리한 표이다.\n응답률은 높았다.\n"
        content = "차세대 반도체 공정에서 미세화 한계가 뚜렷해지고 있음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        assert claim.status == "unmatched"

    def test_uncited_sentence_reported(self):
        content = "국내 시장은 전년 대비 성장세를 이어갈 것으로 전망된다."
        [claim] = align_section(content, {}, {})
        assert claim.status == "uncited"
        assert claim.numbers == []
        assert claim.span is None

    def test_picks_best_chunk_among_markers(self):
        near, far = uuid4(), uuid4()
        content = "반도체 장비 수출액은 3,200억 원으로 집계됐음 [1][2]"
        chunks = {
            far: "노동시장 유연화 논의가 이어지고 있다.\n",
            near: "지난해 반도체 장비 수출액은 3,200억 원으로 집계됐다.\n",
        }
        [claim] = align_section(content, chunks, {1: [far], 2: [near]})
        assert claim.span is not None
        assert claim.span.chunk_id == near
        assert claim.span.number == 2

    def test_missing_chunk_text_is_unmatched_not_crash(self):
        content = "자료가 사라진 뒤에도 이 문장은 근거를 가리키고 있다고 서술한다 [1]"
        [claim] = align_section(content, {}, {1: [uuid4()]})
        assert claim.status == "unmatched"
        assert claim.span is None

    def test_ungrounded_number_reported_per_claim(self):
        cid = uuid4()
        chunk = "국내 생산 능력은 꾸준히 확대되고 있다.\n"
        content = "국내 생산 능력은 42.7% 확대된 것으로 나타났음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        assert claim.ungrounded == ["42.7%"]


class TestGroundedNumbers:
    """수치 → 근거 줄 위치. ungrounded_numbers의 반대 방향을 같은 자로 잰다 -
    무근거가 아니면 반드시 자리가 나오고, 위치는 청크 원문 오프셋이어야 한다."""

    def test_grounded_number_points_to_line(self):
        cid = uuid4()
        chunk = "시장 개요 설명이 먼저 온다.\n지난해 시장 규모는 42.7% 성장했다.\n다른 이야기.\n"
        content = "시장 규모는 42.7% 성장한 것으로 나타났음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        assert claim.ungrounded == []
        [g] = claim.grounded
        assert g.token == "42.7%"
        assert g.chunk_id == cid
        assert "42.7%" in chunk[g.start : g.end]

    def test_comma_difference_matches_normalized(self):
        # 본문 "3,200억" vs 근거 "3200억" - 무근거 판정과 같은 콤마 정규화로 잡아야 한다
        cid = uuid4()
        chunk = "반도체 장비 수출액은 3200억 원으로 집계됐다.\n"
        content = "지난해 반도체 장비 수출액은 3,200억 원으로 집계된 것으로 나타났음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        [g] = claim.grounded
        assert g.token == "3,200"
        assert chunk[g.start : g.end] == "반도체 장비 수출액은 3200억 원으로 집계됐다."

    def test_long_table_row_is_still_findable(self):
        # 수치의 단골 자리인 표 행은 220자를 넘겨 _spans 후보에서 떨어진다 -
        # 위치 탐색은 길이 제한 없이 그 줄을 찾아야 한다.
        cid = uuid4()
        long_row = "| 구분 | " + " | ".join(f"세부 항목 {i}" for i in range(30)) + " | 57.3% |"
        assert len(long_row) > 220
        chunk = "표에 대한 설명이 먼저 온다.\n" + long_row + "\n"
        content = "해당 부문의 국내 시장 점유율은 57.3%로 집계된 것으로 나타났음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        [g] = claim.grounded
        assert g.chunk_id == cid
        assert "57.3" in chunk[g.start : g.end]

    def test_ungrounded_number_is_not_located(self):
        # 무근거로 판정된 수치에 위치를 지어내면 안 된다 - 두 목록은 서로소다.
        cid = uuid4()
        chunk = "국내 생산 능력은 꾸준히 확대되고 있다.\n"
        content = "국내 생산 능력은 42.7% 확대된 것으로 나타났음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        assert claim.ungrounded == ["42.7%"]
        assert claim.grounded == []

    def test_long_paragraph_narrows_to_sentence(self):
        # 웹 원문은 문단이 한 줄이다 - 줄째로 강조하면 문단 전체가 칠해진다(2026-08-14
        # 화면 검증). 수치가 든 문장까지 좁혀야 한다.
        cid = uuid4()
        chunk = (
            "숏폼은 새로운 소비 행태로 자리 잡았다. 응답자의 86.3%가 시청 경험이 있다고 답했다. "
            "플랫폼 간 경쟁도 치열해지고 있다.\n"
        )
        content = "설문 응답자의 86.3%가 숏폼 시청 경험이 있는 것으로 나타났음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        [g] = claim.grounded
        assert chunk[g.start : g.end].strip() == "응답자의 86.3%가 시청 경험이 있다고 답했다."

    def test_picks_line_overlapping_claim_among_duplicates(self):
        # 같은 수치가 여러 줄에 있으면 주장과 어휘가 가장 겹치는 줄을 고른다.
        cid = uuid4()
        chunk = "다른 지표도 42.7% 수준이다.\n수출 증가율은 42.7%로 집계됐다.\n"
        content = "지난해 국내 수출 증가율은 42.7%로 집계된 것으로 나타났음 [1]"
        [claim] = align_section(content, {cid: chunk}, {1: [cid]})
        [g] = claim.grounded
        assert "수출 증가율" in g.text


class TestCrossLingual:
    """한글 주장 + 영문 근거는 어휘 겹침으로 판정할 수 없다.

    실측(2026-08-12, 14절 1,455주장): 영문 근거 866건의 겹침 성적이 일치 1%·불일치 89%로
    사실상 정보량 0이었다. 수치·영문 토큰만으로 채점하도록 바꿔 보니 이번엔 반대로
    '일치' 판정의 40%가 근거 없는 문장이었다 - 겹침 1.00짜리도 포함이다.
    그래서 점수는 대목을 가리키는 데만 쓰고 판정은 LLM으로 넘긴다.
    """

    def test_영문_근거는_점수가_높아도_판정하지_않는다(self) -> None:
        cid = uuid4()
        claim = "MRFR는 2025~2035년 연평균성장률을 30.33%로 제시하며 가장 높은 성장률을 전망함 [1]"
        chunks = {cid: "MRFR projects a CAGR of 30.33% for the 2025-2035 period."}
        (result,) = align_section(claim, chunks, {1: [cid]})
        assert result.status == "crosslingual"
        assert result.span is not None  # 어디를 보면 되는지는 여전히 가리킨다

    def test_한글_근거는_그대로_판정한다(self) -> None:
        cid = uuid4()
        claim = "숏폼 시장은 2026년 1,350억 달러로 성장할 전망임 [1]"
        chunks = {cid: "글로벌 숏폼 시장은 2026년 1,350억 달러 규모로 성장할 것으로 전망된다."}
        (result,) = align_section(claim, chunks, {1: [cid]})
        assert result.status == "aligned"

    def test_영문_문장은_애초에_주장으로_보지_않는다(self) -> None:
        """보고서 본문은 한국어다 - 종결형이 없는 영문 줄은 주장 단위가 아니다.
        교차언어 판정이 필요한 경우는 '한글 주장 + 영문 근거' 한 방향뿐이다."""
        cid = uuid4()
        claim = "The market will reach 135 billion dollars by 2026, growing at 25.6% CAGR [1]"
        chunks = {cid: "The short-form market reaches 135 billion dollars by 2026."}
        assert align_section(claim, chunks, {1: [cid]}) == []
