"""근거 동봉 판정 — 프롬프트 조립과 응답 해석(순수 로직, LLM 호출 없음)."""

from __future__ import annotations

from uuid import uuid4

from src.services.qa.alignment import ClaimAlignment, EvidenceSpan
from src.services.qa.claim_verify import build_prompt, parse_verdicts
from src.services.qa.evidence_findings import findings_from_claims, suspicious_indices


def _claim(text: str, chunk_id=None, score: float = 0.1, numbers=(1,), ungrounded=()):
    span = (
        EvidenceSpan(chunk_id=chunk_id, number=1, start=0, end=10, text="근거 조각", score=score)
        if chunk_id
        else None
    )
    return ClaimAlignment(claim=text, numbers=list(numbers), span=span, ungrounded=list(ungrounded))


class TestBuildPrompt:
    def test_evidence_deduped_and_referenced(self):
        a, b = uuid4(), uuid4()
        claims = [_claim("문장 1", a), _claim("문장 2", a), _claim("문장 3", b)]
        prompt = build_prompt(claims, {a: "가나다 근거", b: "라마바 근거"})
        # 같은 청크를 인용한 문장이 둘이어도 근거 원문은 한 번만 싣는다(비용).
        assert prompt.count("가나다 근거") == 1
        assert "[근거 1]" in prompt and "[근거 2]" in prompt
        # 문장마다 자기 근거 번호가 붙어야 모델이 짝을 안다.
        assert "1. (근거 1) 문장 1" in prompt
        assert "3. (근거 2) 문장 3" in prompt

    def test_missing_chunk_text_skipped(self):
        a = uuid4()
        prompt = build_prompt([_claim("문장", a)], {})
        assert "[근거 원문]" in prompt
        assert "문장" in prompt


class TestParseVerdicts:
    def test_parses_and_indexes_from_one(self):
        content = (
            '```json\n{"verdicts": [{"id": 1, "verdict": "supported", "reason": "동일"},'
            '{"id": 2, "verdict": "not_supported", "reason": "수치 없음"}]}\n```'
        )
        got = parse_verdicts(content, 2)
        assert got[0].is_supported is True
        assert got[1].verdict == "not_supported"
        assert got[1].reason == "수치 없음"

    def test_out_of_range_and_unknown_dropped(self):
        content = (
            '{"verdicts": [{"id": 99, "verdict": "supported"},'
            '{"id": 1, "verdict": "무근거"},{"id": 2, "verdict": "unclear"}]}'
        )
        got = parse_verdicts(content, 2)
        assert set(got) == {1}
        assert got[1].verdict == "unclear"

    def test_garbage_is_empty(self):
        assert parse_verdicts("판정을 할 수 없습니다", 3) == {}


class TestSuspiciousIndices:
    def test_only_cited_low_overlap_claims(self):
        cid = uuid4()
        claims = [
            _claim("겹침 높음", cid, score=0.8),  # aligned - 후보 아님
            _claim("겹침 낮음", cid, score=0.2),  # weak
            _claim("표기 없음", None, numbers=[]),  # uncited - 대조 대상 아님
            _claim("수치 미확인", cid, score=0.9, ungrounded=["42.7%"]),
        ]
        assert suspicious_indices(claims) == [1, 3]

    def test_numeric_drift_in_aligned_claim_enters_funnel(self):
        # 퍼널 계약(2026-08-14): critical 채널 재현율 = 퍼널 x claim_verify인데,
        # '맥락은 근거와 잘 맞고 수치 하나만 어긋난' 문장은 aligned로 분류되기 좋은
        # 모양이라 or c.ungrounded 항이 없으면 판정기에 도달 자체를 못 한다.
        # ClaimAlignment 합성이 아니라 content 수준으로 고정한다(상류 선별이 분모를
        # 깎는 실패가 커버리지·claim_units에 이어 세 번째로 의심된 자리).
        from src.services.qa.alignment import align_section

        cid = uuid4()
        chunk = "국내 생산 능력은 42.7% 확대된 것으로 조사됐고, 관련 설비 투자도 함께 늘었다.\n"
        drifted = "국내 생산 능력은 61.9% 확대된 것으로 조사됐으며 관련 설비 투자도 함께 늘었음 [1]"
        claims = align_section(drifted, {cid: chunk}, {1: [cid]})
        assert claims[0].status == "aligned"  # 맥락 겹침은 높다 - 그래도
        assert "61.9%" in claims[0].ungrounded
        assert suspicious_indices(claims) == [0]  # 퍼널에 들어간다


class TestFindingsWithVerdicts:
    def _row(self):
        from types import SimpleNamespace

        return SimpleNamespace(chapter_number=3, section_number=2)

    def test_supported_claim_drops_warning(self):
        cid = uuid4()
        # 겹침만 보면 '근거 불일치' 3건이지만, 판정에서 뒷받침된다고 나오면 경고가 없다.
        claims = [_claim(f"의역 문장 {i}", cid, score=0.05) for i in range(3)]
        assert findings_from_claims(self._row(), claims, comparable=True) != []
        assert findings_from_claims(self._row(), claims, comparable=True, supported={0, 1, 2}) == []

    def test_refuted_claim_warns_even_with_high_overlap(self):
        cid = uuid4()
        claims = [_claim("겹치지만 뜻이 뒤집힌 문장", cid, score=0.9)]
        rows = findings_from_claims(self._row(), claims, comparable=True, refuted={0})
        assert [r["category"] for r in rows] == ["근거 불일치"]
        assert rows[0]["section_ref"] == "3.2"

    def test_supported_claim_drops_its_numbers(self):
        cid = uuid4()
        claims = [_claim("수치 문장", cid, score=0.9, ungrounded=["42.7%"])]
        assert [
            r["category"] for r in findings_from_claims(self._row(), claims, comparable=True)
        ] == ["무근거 수치"]
        assert findings_from_claims(self._row(), claims, comparable=True, supported={0}) == []

    def test_refuted_numbers_are_critical_lexical_only_warns(self):
        # critical은 판정이 '근거 없음'으로 확인한 수치의 몫 - 어휘 대조만 실패한
        # 수치는 warning까지(단위 환산·표기 차이 오탐, 2026-08-14 실측 26건 전수 오탐).
        cid = uuid4()
        claims = [_claim("수치 문장", cid, score=0.9, ungrounded=["42.7%"])]
        lexical_only = findings_from_claims(self._row(), claims, comparable=True)
        numeric = [r for r in lexical_only if r["category"] == "무근거 수치"]
        assert [r["severity"] for r in numeric] == ["warning"]
        with_verdict = findings_from_claims(self._row(), claims, comparable=True, refuted={0})
        numeric = [r for r in with_verdict if r["category"] == "무근거 수치"]
        assert [r["severity"] for r in numeric] == ["critical"]
        assert "판정 확인" in numeric[0]["detail"]

    def test_relocated_number_downgrades_to_misattribution(self):
        # 2단 판정 - 판정이 '근거 없음'이라도 수치가 코퍼스 어딘가에 실재하면 창작이
        # 아니라 출처를 잘못 단 것(2026-08-21 v6 실측: critical 18건 표본 32/33).
        cid = uuid4()
        claims = [_claim("수치 문장", cid, score=0.9, ungrounded=["48.7%"])]
        rows = findings_from_claims(
            self._row(), claims, comparable=True, refuted={0}, located={"48.7": "환경부 보고서"}
        )
        cats = {r["category"]: r for r in rows}
        assert "무근거 수치" not in cats
        assert cats["출처 오귀속"]["severity"] == "warning"
        assert "환경부 보고서" in cats["출처 오귀속"]["detail"]

    def test_corpus_absent_number_stays_critical(self):
        # 재검색까지 하고도 없으면 진짜 창작 - detail이 재검색을 거쳤음을 밝힌다.
        cid = uuid4()
        claims = [_claim("수치 문장", cid, score=0.9, ungrounded=["48.7%"])]
        rows = findings_from_claims(self._row(), claims, comparable=True, refuted={0}, located={})
        numeric = [r for r in rows if r["category"] == "무근거 수치"]
        assert [r["severity"] for r in numeric] == ["critical"]
        assert "재검색 미발견" in numeric[0]["detail"]

    def test_mixed_tokens_split_between_buckets(self):
        cid = uuid4()
        claims = [_claim("수치 문장", cid, score=0.9, ungrounded=["48.7%", "1,200"])]
        rows = findings_from_claims(
            self._row(), claims, comparable=True, refuted={0}, located={"48.7": "실제 자료"}
        )
        cats = {r["category"] for r in rows}
        assert {"무근거 수치", "출처 오귀속"} <= cats

    def test_english_only_evidence_without_span_is_crosslingual(self):
        # 영문 근거 직역은 어휘 겹침이 0이라 대목(span)조차 못 잡는다 - unmatched로
        # 새면 '근거 불일치'가 부푼다(2026-08-15 실측: 대표 예문 오탐 15건의 주범).
        # crosslingual로 분류돼 경고에서 빠지고 판정 퍼널로는 들어가야 한다.
        from src.services.qa.alignment import align_section

        cid = uuid4()
        chunk = "The CBAM cost equals embedded emissions times the EU ETS price."
        claims = align_section(
            "부담액은 내재배출량에 배출권 가격을 곱해 산정됨 [1]", {cid: chunk}, {1: [cid]}
        )
        assert claims[0].status == "crosslingual"
        assert suspicious_indices(claims) == [0]

    def test_crosslingual_numbers_skipped_without_verdict(self):
        # 교차언어 근거는 어휘로 잴 수 없다 - 판정 없이 세면 전부 오탐이 된다
        # (실측: "72억 달러" vs "$7.2 billion"). 판정이 반박하면 정상적으로 잡힌다.
        cid = uuid4()
        span = EvidenceSpan(
            chunk_id=cid, number=1, start=0, end=10, text="english", score=0.9, comparable=False
        )
        claims = [
            ClaimAlignment(
                claim="세수는 72억 달러 규모임", numbers=[1], span=span, ungrounded=["72"]
            )
        ]
        rows = findings_from_claims(self._row(), claims, comparable=True)
        assert all(r["category"] != "무근거 수치" for r in rows)
        rows = findings_from_claims(self._row(), claims, comparable=True, refuted={0})
        numeric = [r for r in rows if r["category"] == "무근거 수치"]
        assert [r["severity"] for r in numeric] == ["critical"]
