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
