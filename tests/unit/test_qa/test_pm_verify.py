"""PM 검증(pm_verify) — 챕터 그룹핑·JSON 파싱·숫자 다이제스트·경고 정규화 검증.

LLM은 stub, 역할 프롬프트(pm_verify_system)는 실카탈로그를 그대로 읽는다.
저장(persist_findings)은 DB 몫이라 통합 테스트로 미룬다.
"""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import (
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
)
from src.services.qa.pm_verify import (
    MAX_FINDINGS_PER_CHAPTER,
    _to_rows,
    numeric_digest,
    verify_report,
)


class _StubClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        text = self._responses[len(self.calls) % len(self._responses)]
        self.calls.append(request)
        return CompletionResponse(
            content=text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


def _state_two_chapters() -> ProjectState:
    """1장 1절 + 2장 1절, 각 1후보 선택 완료 상태."""
    plans = [
        SectionPlan(chapter_number=1, section_number=1, title="현황"),
        SectionPlan(chapter_number=2, section_number=1, title="분석"),
    ]
    contents = ["고령화율 24.1% 도달 [1]", "예산 1,500억 원 편성 [1]"]
    csets, selections = [], {}
    for plan, content in zip(plans, contents, strict=True):
        cand = SectionCandidate(
            draft=SectionDraft(section_id=plan.section_id, content=content, cited_chunk_ids=[])
        )
        csets.append(SectionCandidateSet(section_id=plan.section_id, candidates=[cand]))
        selections[plan.section_id] = cand.candidate_id
    state = ProjectState(user_id=uuid4(), topic="t", section_plan=plans, section_candidates=csets)
    for sid, cid in selections.items():
        state = state.record_selection(sid, cid)
    return state


class TestNumericDigest:
    def test_extracts_numbers_with_units_deduped(self):
        out = numeric_digest(["성장률 3.2% 및 3.2% 재언급, 1,500억 원 투입", "500명 대상"])
        assert out == ["3.2%", "1,500억 원", "500명"]

    def test_cap(self):
        texts = [" ".join(f"{i}건" for i in range(100))]
        assert len(numeric_digest(texts, cap=10)) == 10

    def test_years_excluded_from_digest(self):
        """연도·기간(N년)은 통계가 아니다 — 다이제스트에 실리면 '2024년 재등장'이
        중복 인용으로 오판된다(2026-08-03 실측: 경고 25건 중 12건이 이 노이즈)."""
        out = numeric_digest(["2024년 기준 시장은 45억 달러, 2007년 이후 3년간 성장"])
        assert all("년" not in token for token in out)


class TestToRows:
    def test_normalizes_and_filters(self):
        manifest = {
            "findings": [
                {
                    "severity": "critical",
                    "category": "법령 시점",
                    "section": "2.1",
                    "detail": "시행 중·추진 중 상충",
                },
                {
                    "severity": "이상한값",
                    "category": "수치 일관성",
                    "detail": "1.2절 45.2% vs 3.1절 45.9%로 불일치",
                },
                {"severity": "warning", "category": None, "detail": "카테고리 없음 불일치"},
                {"detail": "   "},  # 빈 detail → 버림
                "문자열",  # dict 아님 → 버림
            ]
        }
        rows = _to_rows(2, manifest)
        assert len(rows) == 2  # 카테고리 없음("기타")은 축 밖 → 버림
        assert rows[0]["severity"] == "critical"
        assert rows[0]["section_ref"] == "2.1"
        assert rows[1]["severity"] == "warning"  # 미지 severity는 warning으로

    def test_cap_per_chapter(self):
        manifest = {
            "findings": [
                {"severity": "warning", "category": "수치 일관성", "detail": f"d{i} 불일치"}
                for i in range(MAX_FINDINGS_PER_CHAPTER + 10)
            ]
        }
        assert len(_to_rows(1, manifest)) == MAX_FINDINGS_PER_CHAPTER

    def test_off_axis_categories_dropped(self):
        """중복 인용·환각 검출·형식·출처 매칭은 결정적 검출기·근거 동봉 판정의 축이다
        (2026-08-23 v6 전수 검토: LLM 27건 중 17건이 이 축들의 노이즈)."""
        manifest = {
            "findings": [
                {
                    "severity": "warning",
                    "category": "중복 인용",
                    "detail": "46.2%가 재인용되어 상이",
                },
                {"severity": "warning", "category": "환각 검출", "detail": "출처와 불일치 의심"},
                {"severity": "warning", "category": "형식", "detail": "문장이 다르게 종결됨"},
                {"severity": "warning", "category": "출처 매칭", "detail": "출처 번호 불일치"},
                {
                    "severity": "warning",
                    "category": "수치 일관성",
                    "detail": "1.2절 45.2% vs 3.1절 45.9%로 불일치",
                },
            ]
        }
        rows = _to_rows(3, manifest)
        assert [r["category"] for r in rows] == ["수치 일관성"]

    def test_assertless_findings_dropped(self):
        """충돌 단정 없이 '확인 필요'만 말하는 행은 경고가 아니라 할 일 목록이다 -
        프롬프트로 금지해도 모델이 내므로 코드가 최종 관문(v6 실측: 환각 검출 7건 전부)."""
        manifest = {
            "findings": [
                {
                    "severity": "warning",
                    "category": "수치 일관성",
                    "detail": "출처 24가 440개사·570TWh 수치를 뒷받침하는지 확인 필요",
                },
                {
                    "severity": "warning",
                    "category": "수치 일관성",
                    "detail": "467개로 동일하게 인용되어 중복 서술됨. 가독성 저하",
                },
                {
                    "severity": "warning",
                    "category": "수치 일관성",
                    "detail": "2.1절 91억 유로 vs 2.2절 90억 달러로 상이함. 정합성 확인 필요",
                },
            ]
        }
        rows = _to_rows(2, manifest)
        # 단정(상이)이 있으면 꼬리에 '확인 필요'가 붙어도 유지한다 - 어휘가 아니라 단정 유무.
        assert len(rows) == 1
        assert "91억" in rows[0]["detail"]

    def test_structured_values_beat_vocabulary(self):
        """일반화의 핵심 - 충돌 판정의 정본은 값 필드 구조다. 단정 어휘(_ASSERT_RE)에
        없는 표현으로 써도 두 값이 채워졌고 서로 다르면 유지하고(어휘 과적합 소거),
        두 값이 정규화 후 같으면 재언급 지적이라 버린다."""
        manifest = {
            "findings": [
                {
                    "severity": "warning",
                    "category": "수치 일관성",
                    "value_a": "91억 유로",
                    "loc_a": "2.1",
                    "value_b": "90억 달러",
                    "loc_b": "2.2",
                    "detail": "2.1절과 2.2절의 세수 추정치가 서로 일치하지 않는다",
                },
                {
                    "severity": "warning",
                    "category": "수치 일관성",
                    "value_a": "508TWh",
                    "value_b": "508 TWh",
                    "detail": "동일 수치가 두 절에서 반복 인용됨",
                },
            ]
        }
        rows = _to_rows(2, manifest)
        assert len(rows) == 1
        assert rows[0]["_values"] == ["91억 유로", "90억 달러"]

    def test_same_quantity_different_notation_dropped(self):
        """다보고서 실측(2026-08-23)의 새 노이즈 계급 - 같은 값 다른 표기('482.7억' vs
        '482억 7,000만 달러')는 충돌이 아니다. 크기가 같아도 통화가 상충하면 남긴다."""

        def finding(a: str, b: str) -> dict:
            return {
                "severity": "warning",
                "category": "수치 일관성",
                "value_a": a,
                "value_b": b,
                "detail": f"{a} vs {b} 표기",
            }

        manifest = {
            "findings": [
                finding("482.7억", "482억 7,000만 달러"),
                finding("3만 6,000명", "3.6만 명"),
                finding("200개", "200여 개"),
                finding("520억", "520억 달러"),
                finding("90억 달러", "90억 유로"),
                finding("8,357억 달러", "8,356억 달러"),
            ]
        }
        rows = _to_rows(2, manifest)
        assert [r["_values"] for r in rows] == [
            ["90억 달러", "90억 유로"],  # 통화 상충은 실충돌 - 유지
            ["8,357억 달러", "8,356억 달러"],  # 값 차이 - 유지
        ]


class TestVerifyReport:
    async def test_one_call_per_chapter_and_rows_collected(self):
        stub = _StubClient(
            [
                '```json\n{"findings": []}\n```',
                '```json\n{"findings": [{"severity": "warning", "category": "수치 일관성", '
                '"section": "2.1", '
                '"detail": "고령화율이 1.1절 24.1%와 다르게 24.6%로 인용됨"}]}\n```',
            ]
        )
        rows = await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        assert len(stub.calls) == 2  # 챕터당 정확히 1콜 (비용 캡)
        assert [r["chapter_number"] for r in rows] == [2]
        assert rows[0]["category"] == "수치 일관성"

    async def test_same_value_pair_reported_once_across_chapters(self):
        """선행 다이제스트 때문에 같은 값 충돌이 챕터마다 다시 나온다(v6 실측: 90억/91억
        3회) - 값 2개 이상이 겹치면 처음 것만 남긴다."""
        finding = (
            '{"severity": "warning", "category": "수치 일관성", "section": "1.1", '
            '"detail": "회원사가 508TWh와 570TWh로 상이하게 인용됨"}'
        )
        stub = _StubClient([f'```json\n{{"findings": [{finding}]}}\n```'])
        rows = await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        assert len(stub.calls) == 2
        assert len(rows) == 1  # 두 챕터가 같은 값 쌍을 내도 한 번만

    async def test_ghost_value_findings_dropped(self):
        """경고가 인용한 값이 본문 어디에도 없으면 경고 자체가 창작 - 값 실증으로 폐기."""
        finding = (
            '{"severity": "warning", "category": "수치 일관성", "section": "1.1", '
            '"value_a": "24.1%", "value_b": "99.9%", '
            '"detail": "고령화율이 24.1%와 99.9%로 상이"}'
        )
        stub = _StubClient([f'```json\n{{"findings": [{finding}]}}\n```'])
        rows = await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        assert rows == []

    async def test_grounded_values_kept_and_internal_key_stripped(self):
        """값 실증의 눈금은 '지금까지의 문서' - 1장 시점엔 2장의 값(1,500억)이 없어
        창작으로 떨어지고, 2장 시점엔 둘 다 실재라 유지된다. _values는 저장 스키마
        밖이므로 반환 전에 걷는다."""
        finding = (
            '{"severity": "warning", "category": "수치 일관성", "section": "1.1", '
            '"value_a": "24.1%", "value_b": "1,500억 원", '
            '"detail": "지표가 24.1%와 1,500억 원으로 표기가 갈림"}'
        )
        stub = _StubClient([f'```json\n{{"findings": [{finding}]}}\n```'])
        rows = await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        assert [r["chapter_number"] for r in rows] == [2]
        assert "_values" not in rows[0]

    async def test_prev_chapter_digest_flows_to_next_call(self):
        stub = _StubClient(['```json\n{"findings": []}\n```'])
        await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        first, second = (c.messages[0].content for c in stub.calls)
        # 1장 호출에는 선행 다이제스트가 없고, 2장 호출에는 1장의 수치가 실린다.
        assert "이미 인용된 수치" not in first
        assert "24.1%" in second

    async def test_verification_system_prompt_loaded(self):
        stub = _StubClient(['```json\n{"findings": []}\n```'])
        await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        system = stub.calls[0].system
        assert system is not None
        assert "검증" in system  # pm_verify_system 실카탈로그 로드 확인
        assert "JSON" in system  # 출력 계약 부착 확인
