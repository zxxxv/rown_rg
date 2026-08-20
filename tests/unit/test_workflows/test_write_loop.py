"""write 루프 오케스트레이션 검증 — fake retriever + stub LLM으로 DB/네트워크 없이 완결."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import (
    CheckSeverity,
    GateResult,
    RetrievedChunk,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    StaticCheckReport,
)
from src.workflows import cancel
from src.workflows.cancel import RunCancelled
from src.workflows.write_loop import (
    apply_selection,
    check_assembled,
    overlay_working_copy,
    qa_select_payload,
    run_write_loop,
)


class _StubClient:
    """모든 호출에 같은 텍스트를 돌려주는 가짜 LLMClient."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        return CompletionResponse(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


def _hard(check: str, passed: bool) -> GateResult:
    return GateResult(check=check, severity=CheckSeverity.HARD, passed=passed)


def _candset(section_id, n: int = 2) -> SectionCandidateSet:
    """모두 HARD 통과하는 후보 n개 묶음."""
    cands = [
        SectionCandidate(
            draft=SectionDraft(section_id=section_id, content=f"후보{i}", cited_chunk_ids=[]),
            report=StaticCheckReport(results=[_hard("renderable", True)]),
        )
        for i in range(n)
    ]
    return SectionCandidateSet(section_id=section_id, candidates=cands)


# ---------- 사실 대장 + builds_on (적립·배치·주입) ----------


class TestLedgerAndBuildsOn:
    def _chunk(self) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
        )

    async def test_completed_section_accrues_ledger_entries(self):
        """절 완료 직후 확정값이 meta.ledger_entries로 적립된다 - 마커는 로컬 번호."""
        s1 = SectionPlan(chapter_number=4, section_number=1, title="예산")
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[s1])
        chunk = self._chunk()

        async def retrieve(section):
            return [chunk]

        body = (
            "본문 서술이 길게 이어집니다. " * 20
            + "\n| 구분 | 값 |\n|---|---|\n| 총사업비 | 1.2조 원 (출처 1) |\n"
        )
        result = await run_write_loop(state, retrieve=retrieve, client=_StubClient(body), n=1)
        meta = result.section_meta[s1.section_id]
        entries = meta.get("ledger_entries") or []
        assert any(e["metric"] == "총사업비" and e["value"] == "1.2" for e in entries)
        # 로컬 마커 1 -> 풀의 첫 청크 id로 해소
        target = next(e for e in entries if e["metric"] == "총사업비")
        assert str(chunk.chunk_id) in target["chunk_ids"]

    async def test_dependent_section_receives_injection(self):
        """builds_on 절은 앞 배치의 확정값을 guidance로 받고, 원 청크가 풀에 덧붙는다."""
        s1 = SectionPlan(chapter_number=4, section_number=1, title="예산")
        s5 = SectionPlan(chapter_number=4, section_number=5, title="시사점", builds_on=["4.1"])
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[s1, s5])
        base = self._chunk()
        loaded: list[list] = []

        async def retrieve(section):
            return [base] if section.section_number == 1 else [self._chunk()]

        async def chunk_loader(ids):
            loaded.append(list(ids))
            return [
                RetrievedChunk(chunk_id=i, source_id=uuid4(), content="원 근거", score=1.0)
                for i in ids
            ]

        class _CapturingClient(_StubClient):
            def __init__(self, text):
                super().__init__(text)
                self.systems: dict[str, str] = {}

        body = (
            "본문 서술이 길게 이어집니다. " * 20
            + "\n| 구분 | 값 |\n|---|---|\n| 총사업비 | 1.2조 원 (출처 1) |\n"
        )
        stub = _CapturingClient(body)
        result = await run_write_loop(
            state, retrieve=retrieve, client=stub, n=1, chunk_loader=chunk_loader
        )
        assert len(result.section_candidates) == 2
        # 주입 블록이 프롬프트 어딘가에 실렸다 - 확정값 문구로 확인
        joined = "\n".join(
            (r.system or "") + "\n" + "\n".join(m.content for m in r.messages) for r in stub.calls
        )
        assert "앞 절에서 확정된 값" in joined
        assert "총사업비: 1.2조원" in joined.replace("1.2조 원", "1.2조원") or "총사업비" in joined
        # 원 청크 로드가 실제로 일어났다(같은 풀에 있으면 생략될 수 있어 base 기준)
        assert loaded and str(base.chunk_id) in [str(x) for x in loaded[0]]

    async def test_missing_dependency_warns_but_proceeds(self):
        """대상 절이 확정값을 못 남겨도 의존 절은 막히지 않는다(절 격리 원칙)."""
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=1, section_number=2, title="종합", builds_on=["1.1"])
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[s1, s2])

        async def retrieve(section):
            return [self._chunk()]

        # 표·명시 값이 없는 본문 -> 1.1의 대장이 빈다
        result = await run_write_loop(
            state, retrieve=retrieve, client=_StubClient("서술만 있는 본문 [1] " * 30), n=1
        )
        assert len(result.section_candidates) == 2
        meta = result.section_meta[s2.section_id]
        assert meta.get("ledger_inject_warnings")

    async def test_kept_meta_seeds_ledger_on_resume(self):
        """증분 재개 - 건너뛴 완성 절의 대장이 state.section_meta로 들어와 주입된다."""
        s5 = SectionPlan(chapter_number=4, section_number=5, title="시사점", builds_on=["4.1"])
        kept_id = uuid4()
        state = ProjectState(
            user_id=uuid4(),
            topic="주제",
            section_plan=[s5],
            section_meta={
                kept_id: {
                    "ledger_entries": [
                        {
                            "metric": "총사업비",
                            "value": "1.2",
                            "unit": "조원",
                            "qualifiers": {},
                            "section_ref": "4.1",
                            "chunk_ids": [],
                            "source_kind": "table",
                        }
                    ]
                }
            },
        )

        async def retrieve(section):
            return [self._chunk()]

        stub = _StubClient("본문 [1] " * 30)
        result = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)
        assert len(result.section_candidates) == 1
        joined = "\n".join(
            (r.system or "") + "\n".join(m.content for m in r.messages) for r in stub.calls
        )
        assert "앞 절에서 확정된 값" in joined


# ---------- run_write_loop (검색→생성→게이트 통합) ----------


class TestRunWriteLoop:
    async def test_produces_gated_candidates_per_section(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[s1, s2])
        chunk = RetrievedChunk(
            chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
        )

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return [chunk]

        # 길게 → bounds 통과, [1] → chunk 인용 resolves.
        stub = _StubClient("이 섹션의 본문입니다. [1] " * 30)
        result = await run_write_loop(state, retrieve=retrieve, client=stub, n=2)

        assert len(result.section_candidates) == 2
        for cset in result.section_candidates:
            assert len(cset.candidates) == 2
            assert len(cset.survivors) == 2  # 전부 HARD 통과
            assert cset.candidates[0].draft.cited_chunk_ids == [chunk.chunk_id]

    async def test_records_evidence_meta_per_section(self):
        """재료 지표는 본문이 아니라 state.section_meta로 — 화면 배지의 유일한 출처."""
        rich = SectionPlan(chapter_number=1, section_number=1, title="풍부", analysts=["정책동향"])
        poor = SectionPlan(chapter_number=1, section_number=2, title="빈약", analysts=["정책동향"])
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[rich, poor])

        def _chunk() -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
            )

        many = [_chunk() for _ in range(40)]

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return many if section.title == "풍부" else [_chunk()]

        result = await run_write_loop(
            state, retrieve=retrieve, client=_StubClient("본문 [1] " * 30), n=1
        )
        assert result.section_meta[rich.section_id]["evidence_count"] == 40
        assert result.section_meta[rich.section_id]["volume_scaled"] is False
        assert result.section_meta[poor.section_id]["evidence_count"] == 1
        assert result.section_meta[poor.section_id]["volume_scaled"] is True

    async def test_empty_plan_yields_no_candidates(self):
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[])

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return []

        result = await run_write_loop(state, retrieve=retrieve, client=_StubClient("x"), n=2)
        assert result.section_candidates == []


# ---------- run_write_loop 병렬화 (순서·동일성·상한·취소) ----------


def _mk_state(titles: list[str]) -> tuple[ProjectState, RetrievedChunk]:
    plans = [
        SectionPlan(chapter_number=i + 1, section_number=1, title=t) for i, t in enumerate(titles)
    ]
    state = ProjectState(user_id=uuid4(), topic="주제", section_plan=plans)
    chunk = RetrievedChunk(
        chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
    )
    return state, chunk


class TestRunWriteLoopParallel:
    async def test_output_in_plan_order_despite_reversed_completion(self):
        """앞 절이 느려 완료 순서가 뒤집혀도 결과는 plan 순서 — gather 입력 순서 보존."""
        state, chunk = _mk_state(["느린절", "빠른절"])
        completed: list[str] = []

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            if section.title == "느린절":
                await asyncio.sleep(0.05)
            completed.append(section.title)
            return [chunk]

        result = await run_write_loop(
            state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
        )
        assert completed == ["빠른절", "느린절"]  # 실제로 병렬로 돌았다
        assert [cs.section_id for cs in result.section_candidates] == [
            p.section_id for p in state.section_plan
        ]
        assert list(result.section_meta) == [p.section_id for p in state.section_plan]

    async def test_parallel_result_equals_serial(self, monkeypatch):
        """같은 입력이면 동시성 1(직렬)과 4(병렬)의 산출이 내용까지 동일하다."""
        state, chunk = _mk_state(["개요", "분석", "전망"])

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return [chunk]

        stub = _StubClient("이 섹션의 본문입니다. [1] " * 30)

        def _essence(result: ProjectState) -> list[tuple]:
            return [
                (
                    cs.section_id,
                    [c.draft.content for c in cs.candidates],
                    [c.draft.cited_chunk_ids for c in cs.candidates],
                )
                for cs in result.section_candidates
            ]

        monkeypatch.setattr(settings, "write_section_concurrency", 1)
        serial = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)
        monkeypatch.setattr(settings, "write_section_concurrency", 4)
        parallel = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)

        assert _essence(serial) == _essence(parallel)
        assert serial.section_meta == parallel.section_meta

    async def test_semaphore_caps_concurrency(self, monkeypatch):
        """동시 실행 절 수가 write_section_concurrency를 넘지 않는다(그리고 병렬이긴 하다)."""
        monkeypatch.setattr(settings, "write_section_concurrency", 2)
        state, chunk = _mk_state([f"{i}절" for i in range(6)])
        active = 0
        peak = 0

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return [chunk]

        await run_write_loop(
            state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
        )
        assert peak == 2

    async def test_cancel_stops_queued_sections(self, monkeypatch):
        """작성 중 취소 — 큐에 있던 절은 시작 전에 멈추고 RunCancelled가 올라간다."""
        monkeypatch.setattr(settings, "write_section_concurrency", 1)
        state, chunk = _mk_state(["첫절", "둘째절", "셋째절"])
        retrieved: list[str] = []

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            retrieved.append(section.title)
            if section.title == "첫절":
                cancel.request(state.project_id)
            return [chunk]

        try:
            with pytest.raises(RunCancelled):
                await run_write_loop(
                    state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
                )
        finally:
            cancel.clear(state.project_id)
        assert retrieved == ["첫절"]  # 뒤 절들은 시작조차 안 했다

    async def test_cancel_aborts_inflight_sibling(self, monkeypatch):
        """취소 전파 시 진행 중이던 다른 절 태스크도 실제로 중단(cancel)된다."""
        monkeypatch.setattr(settings, "write_section_concurrency", 2)
        state, chunk = _mk_state(["막힌절", "신호절", "대기절"])
        blocked = asyncio.Event()
        aborted = asyncio.Event()

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            if section.title == "막힌절":
                blocked.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    aborted.set()
                    raise
            if section.title == "신호절":
                await blocked.wait()
                cancel.request(state.project_id)
            return [chunk]

        try:
            with pytest.raises(RunCancelled):
                await run_write_loop(
                    state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
                )
        finally:
            cancel.clear(state.project_id)
        assert aborted.is_set()


# ---------- 절 단위 실패 비삼킴 (2026-08-13 실사고 재발 방지) ----------


class TestSectionFailureSurfacing:
    """빈 절·토막 절이 '완성' 뒤에 숨지 않는다 — 실패는 meta에 기록되고 절만 격리된다."""

    async def test_llm_error_isolated_to_failed_section(self):
        """한 절의 LLM 실패(백오프 소진)가 다른 절의 완성을 버리지 않는다."""
        from src.clients.llm.exceptions import LLMAPIError

        state, chunk = _mk_state(["정상절", "실패절"])

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return [chunk]

        class _FailOne(_StubClient):
            async def complete(self, request: CompletionRequest) -> CompletionResponse:
                if "실패절" in request.messages[0].content:
                    raise LLMAPIError("overloaded (모의)")
                return await super().complete(request)

        result = await run_write_loop(
            state, retrieve=retrieve, client=_FailOne("본문입니다. [1] " * 30), n=1
        )
        ok_plan, bad_plan = state.section_plan
        by_id = {cs.section_id: cs for cs in result.section_candidates}
        assert by_id[ok_plan.section_id].survivors  # 정상 절은 산다
        assert by_id[bad_plan.section_id].candidates == []  # 실패 절은 빈 묶음
        meta = result.section_meta[bad_plan.section_id]
        assert meta["write_failed"] is True
        assert "LLM 호출 실패" in meta["fail_detail"]
        # 조립 게이트가 이 상태를 실패로 본다 — completed로 못 넘어간다.
        from src.workflows.write_loop import auto_select_survivors

        _, gate = check_assembled(auto_select_survivors(result))
        assert gate.passed is False

    async def test_truncated_response_excluded_and_recorded(self):
        """max_tokens 컷 토막은 후보에서 제외되고(재생성 1회 포함) 실패로 기록된다."""

        class _Truncated(_StubClient):
            async def complete(self, request: CompletionRequest) -> CompletionResponse:
                self.calls.append(request)
                return CompletionResponse(
                    content="문장 중간에 끊긴 본문 (출처 ",
                    input_tokens=1,
                    output_tokens=1,
                    model=request.model,
                    stop_reason="max_tokens",
                )

        state, chunk = _mk_state(["절단절"])

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return [chunk]

        stub = _Truncated("")
        result = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)
        plan = state.section_plan[0]
        cset = result.section_candidates[0]
        assert cset.survivors == []  # 토막이 '완성 절'로 채택되지 않는다
        assert len(stub.calls) == 2  # 원호출 + 재생성 1회
        meta = result.section_meta[plan.section_id]
        assert meta["write_failed"] is True
        assert "미완결" in meta["fail_detail"]


# ---------- qa_select_payload (survivors 필터 + 경고 노출) ----------


class TestQaSelectPayload:
    def test_shows_only_survivors_with_warnings(self):
        section_id = uuid4()
        good = SectionCandidate(
            draft=SectionDraft(section_id=section_id, content="좋은 본문", cited_chunk_ids=[]),
            report=StaticCheckReport(
                results=[
                    _hard("citation_resolves", True),
                    GateResult(
                        check="bounds",
                        severity=CheckSeverity.SOFT,
                        passed=False,
                        detail="너무 짧음",
                    ),
                ]
            ),
        )
        bad = SectionCandidate(
            draft=SectionDraft(section_id=section_id, content="나쁜", cited_chunk_ids=[]),
            report=StaticCheckReport(results=[_hard("citation_resolves", False)]),
        )
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_candidates=[SectionCandidateSet(section_id=section_id, candidates=[good, bad])],
        )
        payload = qa_select_payload(state)
        sections = payload["sections"]
        assert isinstance(sections, list)
        cands = sections[0]["candidates"]
        assert len(cands) == 1  # HARD 실패한 bad 제외
        assert cands[0]["candidate_id"] == str(good.candidate_id)
        assert cands[0]["warnings"][0]["check"] == "bounds"
        assert sections[0]["all_excluded"] is False

    def test_all_excluded_flag(self):
        section_id = uuid4()
        bad = SectionCandidate(
            draft=SectionDraft(section_id=section_id, content="x", cited_chunk_ids=[]),
            report=StaticCheckReport(results=[_hard("renderable", False)]),
        )
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_candidates=[SectionCandidateSet(section_id=section_id, candidates=[bad])],
        )
        payload = qa_select_payload(state)
        sections = payload["sections"]
        assert sections[0]["all_excluded"] is True
        assert sections[0]["candidates"] == []


# ---------- apply_selection + check_assembled ----------


class TestSelectionAndAssembly:
    def test_selection_records_and_assembles_in_plan_order(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        cs1, cs2 = _candset(s1.section_id), _candset(s2.section_id)
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_plan=[s1, s2],
            section_candidates=[cs1, cs2],
        )
        selections = {
            str(s1.section_id): str(cs1.candidates[1].candidate_id),
            str(s2.section_id): str(cs2.candidates[0].candidate_id),
        }
        state = apply_selection(state, selections)
        drafts, result = check_assembled(state)
        assert result.passed is True
        # 순서 = plan, 내용 = 선택된 후보
        assert [d.content for d in drafts] == ["후보1", "후보0"]

    def test_missing_selection_fails_structure(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        cs1, cs2 = _candset(s1.section_id), _candset(s2.section_id)
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_plan=[s1, s2],
            section_candidates=[cs1, cs2],
        )
        # s2 선택 누락
        state = apply_selection(state, {str(s1.section_id): str(cs1.candidates[0].candidate_id)})
        drafts, result = check_assembled(state)
        assert len(drafts) == 1
        assert result.passed is False
        assert "미작성 절 1개" in result.detail
        assert "2.1" in result.detail


# ---------- overlay_working_copy (검토 중 편집 → 조립 반영) ----------


class TestOverlayWorkingCopy:
    def test_row_content_overrides_candidates(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        cs1 = _candset(s1.section_id)
        state = ProjectState(
            user_id=uuid4(), topic="t", section_plan=[s1], section_candidates=[cs1]
        )
        cited = [uuid4()]
        state = overlay_working_copy(state, {s1.section_id: ("사람이 고친 본문", cited)})
        for cand in state.section_candidates[0].candidates:
            assert cand.draft.content == "사람이 고친 본문"
            assert cand.draft.cited_chunk_ids == cited

    def test_empty_or_missing_row_keeps_candidates(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        cs1, cs2 = _candset(s1.section_id), _candset(s2.section_id)
        state = ProjectState(
            user_id=uuid4(), topic="t", section_plan=[s1, s2], section_candidates=[cs1, cs2]
        )
        # s1은 빈 내용(failed 행), s2는 행 없음 — 둘 다 payload 후보 유지
        state = overlay_working_copy(state, {s1.section_id: ("   ", [])})
        assert state.section_candidates[0].candidates[0].draft.content == "후보0"
        assert state.section_candidates[1].candidates[0].draft.content == "후보0"

    def test_filled_empty_section_promoted_and_selected(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        empty = SectionCandidateSet(section_id=s1.section_id, candidates=[])
        state = ProjectState(
            user_id=uuid4(), topic="t", section_plan=[s1], section_candidates=[empty]
        )
        state = overlay_working_copy(state, {s1.section_id: ("직접 채운 본문", [])})
        drafts, result = check_assembled(state)
        assert result.passed is True
        assert [d.content for d in drafts] == ["직접 채운 본문"]


# ---------- 서사 사슬 (실험 C: 장 내 순차 + 요약 전달) ----------


class TestNarrativeChain:
    def _chunk(self) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
        )

    def _plan(self) -> list[SectionPlan]:
        return [
            SectionPlan(chapter_number=1, section_number=1, title="현황"),
            SectionPlan(chapter_number=1, section_number=2, title="과제"),
            SectionPlan(chapter_number=2, section_number=1, title="제도개요"),
        ]

    @staticmethod
    async def _fake_summarize(*, label, title, content, **_kw):
        return {"section": label, "title": title, "summary": f"{label} 요약문", "topics": [label]}

    async def test_chapter_mode_passes_same_chapter_summaries_only(self, monkeypatch):
        """1.2는 1.1 요약을 받고, 2.1(다른 장)은 1장 요약을 받지 않는다(장 간 병렬)."""
        monkeypatch.setattr(settings, "write_narrative_chain", "chapter")
        monkeypatch.setattr("src.workflows.write_loop.summarize_section", self._fake_summarize)
        plan = self._plan()
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=plan)

        async def retrieve(section):
            return [self._chunk()]

        stub = _StubClient("본문 서술이 길게 이어집니다. " * 30)
        result = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)

        with_chain = [str(c.messages) for c in stub.calls if "1.1 요약문" in str(c.messages)]
        assert len(with_chain) == 1, "1.1 요약은 1.2 프롬프트에만 실려야 한다"
        assert "과제" in with_chain[0]  # 받은 쪽이 1.2인지 확인
        assert all(
            "요약문" not in str(c.messages) or "1.1 요약문" in str(c.messages) for c in stub.calls
        )
        # 완료 절마다 사슬 엔트리가 meta에 적립된다(재개 복원용)
        for s in plan:
            assert result.section_meta[s.section_id].get("chain_summary", {}).get("section")

    async def test_full_mode_crosses_chapters(self, monkeypatch):
        """full 모드에선 2.1이 1장 요약(1.1·1.2)을 받는다 - 누적 전달."""
        monkeypatch.setattr(settings, "write_narrative_chain", "full")
        monkeypatch.setattr("src.workflows.write_loop.summarize_section", self._fake_summarize)
        plan = self._plan()
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=plan)

        async def retrieve(section):
            return [self._chunk()]

        stub = _StubClient("본문 서술이 길게 이어집니다. " * 30)
        await run_write_loop(state, retrieve=retrieve, client=stub, n=1)
        crossing = [
            str(c.messages)
            for c in stub.calls
            if "1.1 요약문" in str(c.messages) and "1.2 요약문" in str(c.messages)
        ]
        assert len(crossing) == 1, "1.1+1.2 요약을 함께 받는 건 2.1뿐이어야 한다"
        assert "제도개요" in crossing[0]

    async def test_off_mode_never_summarizes(self, monkeypatch):
        """기본(off)에선 요약 호출도 주입도 없다 - A/B 기준선 보존."""
        monkeypatch.setattr(settings, "write_narrative_chain", "off")
        called = []

        async def spy(**kw):
            called.append(kw)
            return None

        monkeypatch.setattr("src.workflows.write_loop.summarize_section", spy)
        plan = self._plan()
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=plan)

        async def retrieve(section):
            return [self._chunk()]

        stub = _StubClient("본문 서술이 길게 이어집니다. " * 30)
        result = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)
        assert called == []
        assert all("chain_summary" not in result.section_meta[s.section_id] for s in plan)

    async def test_summarize_failure_does_not_block(self, monkeypatch):
        """요약이 전부 실패해도 작성은 완주한다 - 사슬은 보조 정보다."""
        monkeypatch.setattr(settings, "write_narrative_chain", "chapter")

        async def broken(**kw):
            return None

        monkeypatch.setattr("src.workflows.write_loop.summarize_section", broken)
        plan = self._plan()
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=plan)

        async def retrieve(section):
            return [self._chunk()]

        stub = _StubClient("본문 서술이 길게 이어집니다. " * 30)
        result = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)
        assert len(result.section_candidates) == 3
        assert all(cs.survivors for cs in result.section_candidates)
