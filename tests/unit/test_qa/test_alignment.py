"""문장 ↔ 근거 대목 정렬 — 어휘 겹침만으로 청크 안의 줄까지 좁히는 결정적 로직."""

from __future__ import annotations

from uuid import uuid4

from src.services.qa.alignment import (
    ALIGNED_THRESHOLD,
    align_section,
    overlap_score,
)
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

    def test_empty_claim_is_zero(self):
        assert overlap_score("[1]", "아무 근거") == 0.0


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
