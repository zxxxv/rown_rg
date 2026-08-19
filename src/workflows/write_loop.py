"""write 루프 오케스트레이션 — 섹션별 검색→생성→게이트, 그리고 QA_SELECT 전후 순수 헬퍼.

파이프라인 척추(pipeline.py)와 runner가 이 함수들을 호출한다. 의존성(검색·생성)은
주입식이라 실검색/실LLM 없이 단위 테스트된다.

무한성 불변식: 후보 N은 상수(1회 fan-out), 게이트는 결정적(종료 보장), 최종 선택은 사람.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

import structlog

from src.clients.llm.base import LLMClient
from src.clients.llm.exceptions import LLMClientError
from src.core.builds_on import batches as builds_on_batches
from src.core.config import settings
from src.core.section_plan import dump_section_plan, load_section_plan
from src.core.state import ProjectState
from src.core.types import (
    CheckSeverity,
    GateResult,
    RetrievedChunk,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
)
from src.services.generation.candidates import (
    DEFAULT_MODEL,
    generate_section_candidates,
)
from src.services.generation.split_writer import (
    generate_section_split,
    plan_part_count,
)
from src.services.generation.writer_context import build_writer_context, scale_for_evidence
from src.services.ledger import extract_entries, format_injection, select_for_refs
from src.services.qa.gate import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_CHARS,
    check_structure_complete,
    gate_candidates,
)
from src.services.retrieval.section import SectionRetriever
from src.workflows import cancel
from src.workflows.events import emit_step

logger = structlog.get_logger(__name__)

# 재생성 시 온도 — 같은 온도로 다시 부르면 같은 실패를 반복할 확률이 높다.
RETRY_TEMPERATURE = 1.0

# 절 완성 즉시 초안을 영속화하는 훅(미리보기 증분 표시) — None이면 생략.
# (state, plan, 생존 draft|None)을 받는다. 주입식이라 단위 테스트는 DB 없이 돈다.
DraftStore = Callable[[ProjectState, SectionPlan, SectionDraft | None], Awaitable[None]]

# 청크 id 목록 → RetrievedChunk. builds_on 주입이 앞 절 확정값의 원 청크를 의존 절
# 근거 풀에 덧붙일 때 쓴다 — 풀에 있어야 작성기가 (출처 n)으로 인용하고 그 마커가
# renumber를 거쳐 원 근거로 해소된다(인용 사슬 무결). None이면 주입은 값만 싣고
# 인용 번호 없이 나간다(테스트·미리보기 경로).
ChunkLoader = Callable[[list[UUID]], Awaitable[list[RetrievedChunk]]]


def design_plan_note(state: ProjectState, section: SectionPlan) -> str:
    """이 절의 승인된 실행 계획 → 작성 guidance 블록. 없으면 빈 문자열.

    출처는 config["_design_plan"](설계 브리프 게이트에서 사람이 보고 승인한 계획).
    runner._commit_design_plan이 커밋하고, 목차가 바뀌면 함께 버려진다(어긋남 방지).
    """
    raw = state.options.get("_design_plan") if isinstance(state.options, dict) else None
    if not isinstance(raw, dict):
        return ""
    note = raw.get(str(section.section_id))
    if not isinstance(note, dict):
        return ""
    labels = (("goal", "목표"), ("source_strategy", "자료 활용"), ("writing_plan", "구성"))
    lines = [f"- {label}: {note[key]}" for key, label in labels if str(note.get(key) or "").strip()]
    if not lines:
        return ""
    return "설계 검토에서 승인된 이 절의 실행 계획:\n" + "\n".join(lines)


async def run_write_loop(
    state: ProjectState,
    *,
    retrieve: SectionRetriever,
    client: LLMClient | None = None,
    n: int | None = None,
    model: str = DEFAULT_MODEL,
    plan_model: str | None = None,
    draft_store: DraftStore | None = None,
    analyst_catalog: dict[str, Any] | None = None,
    rules: list[str] | None = None,
    chunk_loader: ChunkLoader | None = None,
) -> ProjectState:
    """section_plan의 각 섹션을 검색→후보 생성→정적 게이트로 처리해 state에 적재.

    검색은 주입된 retrieve로, 생성은 generate_section_candidates(client 주입 가능)로.
    섹션별 WriterContext가 페르소나·방향·분량 목표를 프롬프트에 주입하고, 에이전트
    volume_target이 있으면 정적 게이트의 길이 경계도 그것을 따른다.
    게이트 판정까지 마친 SectionCandidateSet들을 state.section_candidates에 넣어 돌려준다.

    절 처리는 write_section_concurrency 상한의 병렬(2026-08-11) — 절끼리 의존성이
    없어서다. 결과 순서·내용은 직렬과 동일하고(타이밍만 다름), 절 내부 분할 파트는
    문맥 연결 때문에 직렬을 유지한다.
    """
    pid = state.project_id
    n = n if n is not None else settings.write_candidates_n
    # 완료 순서대로 채워지는 공유 dict — 단일 이벤트 루프라 태스크 간 mutation이 안전하다.
    section_meta: dict[UUID, dict] = {}

    # 사실 대장 — 절 라벨("4.1") → 이번 실행에서 적립된 엔트리. 증분 재개로 건너뛴
    # 완성 절의 몫은 행 meta에서 복원돼 state.section_meta로 들어온다(엔트리가
    # section_ref를 품고 있어 라벨 매핑이 자급된다).
    ledger_by_label: dict[str, list[dict]] = {}
    for kept in (state.section_meta or {}).values():
        for e in kept.get("ledger_entries") or []:
            ref = e.get("section_ref")
            if ref:
                ledger_by_label.setdefault(str(ref), []).append(e)

    async def _ledger_injection(
        section: SectionPlan, chunks: list[RetrievedChunk]
    ) -> tuple[list[RetrievedChunk], str]:
        """builds_on 절의 근거 풀 확장 + 주입 블록 생성.

        확정값의 원 청크를 풀에 덧붙여야 작성기가 (출처 n)으로 인용하고 그 마커가
        renumber를 거쳐 원 근거로 해소된다(인용 사슬 무결 — 판정축 ③). 대상이 비어
        있어도 막지 않는다 — 빈 주입 + meta 경고로 진행(절 격리 원칙).
        """
        entries, warns = select_for_refs(list(section.builds_on), ledger_by_label)
        label = f"{section.chapter_number}.{section.section_number}"
        if warns:
            ledger_warns[section.section_id] = warns
            logger.warning(
                "write_loop.ledger_injection", project_id=str(pid), section=label, warnings=warns
            )
        if not entries:
            return chunks, ""
        have = {str(c.chunk_id) for c in chunks}
        need = [UUID(cid) for e in entries for cid in e.get("chunk_ids", []) if cid not in have]
        if need and chunk_loader is not None:
            try:
                extra = await chunk_loader(list(dict.fromkeys(need)))
            except Exception:
                # 청크 로드 실패가 절 작성을 막으면 안 된다 — 값은 그대로 싣되 인용
                # 번호 없이 나간다(작성기는 값을 쓰고 출처 표기는 생략하게 된다).
                logger.warning(
                    "write_loop.ledger_chunks_failed", project_id=str(pid), section=label
                )
                extra = []
            chunks = [*chunks, *extra]
        citable = [c for c in chunks if not c.is_summary]
        local_no = {str(c.chunk_id): i + 1 for i, c in enumerate(citable)}
        note = format_injection(entries, local_no)
        logger.info(
            "write_loop.ledger_injected",
            project_id=str(pid),
            section=label,
            n_entries=len(entries),
            n_extra_chunks=len(need),
        )
        return chunks, note

    # 주입 경고 임시 보관 — section_meta가 검색 뒤에야 만들어져 직접 못 적는다.
    ledger_warns: dict[UUID, list[str]] = {}

    async def _process_section(section: SectionPlan) -> SectionCandidateSet:
        # 절 단위 취소 지점 — 세마포어 대기 중이던 절도 시작 전에 여기서 멈춘다.
        cancel.raise_if_cancelled(pid)
        label = f"본문 작성 · {section.chapter_number}.{section.section_number} {section.title}"
        emit_step(pid, "writing", label, "started")
        ctx = build_writer_context(section, analyst_catalog, rules)
        note = design_plan_note(state, section)
        if note:
            # 설계 검토에서 승인된 실행 계획 — 게이트가 보여준 계획대로 쓰게 하는 계약.
            # 이 주입이 없으면 계획은 화면 장식이고 작성기는 계획을 본 적 없는 채로 쓴다.
            ctx = replace(ctx, guidance="\n\n".join(x for x in (ctx.guidance, note) if x))
        chunks = await retrieve(section)
        if section.builds_on:
            # 사실 대장 주입 — 앞 배치가 적립한 확정값을 구조화된 형태로 싣는다.
            # 서술 요약으로 잇는 실험은 무근거 +39% 순손해였다(services/ledger 참조).
            chunks, ledger_note = await _ledger_injection(section, chunks)
            if ledger_note:
                ctx = replace(
                    ctx,
                    guidance="\n\n".join(x for x in (ctx.guidance, ledger_note) if x),
                )
        # 재료가 목표에 못 미치면 목표를 내린다 — 검색 뒤라야 실제 근거 수를 안다.
        n_evidence = sum(1 for c in chunks if not c.is_summary)
        scaled = scale_for_evidence(ctx, n_evidence)
        # 부족 사실은 본문이 아니라 절 메타로 — 화면이 배지로 알리고 본문은 깨끗하게.
        section_meta[section.section_id] = {
            "evidence_count": n_evidence,
            "volume_scaled": scaled.min_chars != ctx.min_chars,
            "min_chars": scaled.min_chars,
            # builds_on 주입 흔적 — 화면·채점이 "이 절이 무엇을 받았나"를 본다.
            **(
                {"ledger_inject_warnings": ledger_warns[section.section_id]}
                if section.section_id in ledger_warns
                else {}
            ),
            # 프롬프트에 실린 인용 가능 청크를 그 순서 그대로 남긴다(작성기의 [n] 번호 = 여기 i+1).
            # 인용된 것만 남기면 "봤는데 안 쓴 근거"와 "안 보고 쓴 주장"을 구분할 수 없다.
            "pool_chunk_ids": [str(c.chunk_id) for c in chunks if not c.is_summary],
        }
        ctx = scaled

        async def _generate(base_temperature: float = 0.7) -> list[SectionDraft]:
            # volume_target이 단일 호출 출력 한계(4~8천자 실측)를 넘으면 분할 생성.
            # 분할 실패(계획·배정 무너짐, 풀 빈약)는 비치명 — 단일 호출로 폴백한다.
            # 파트 간 문맥 연결 때문에 절 내부는 여전히 직렬이다(병렬은 절 사이만).
            n_parts = plan_part_count(ctx.min_chars)
            if n_parts > 1:
                split_draft = await generate_section_split(
                    section,
                    chunks,
                    n_parts=n_parts,
                    model=model,
                    plan_model=plan_model,
                    client=client,
                    context=ctx,
                    base_temperature=base_temperature,
                    user_id=state.user_id,
                    project_id=state.project_id,
                )
                if split_draft is not None:
                    return [split_draft]
                # 분할이 무너진 채 단일 호출로 간다 - 결과에 흔적을 남겨야 화면이 알린다.
                fell_back = True
            else:
                fell_back = False
            drafts = await generate_section_candidates(
                section,
                chunks,
                n=n,
                model=model,
                client=client,
                context=ctx,
                base_temperature=base_temperature,
                user_id=state.user_id,
                project_id=state.project_id,
            )
            return [d.model_copy(update={"split_fallback": fell_back}) for d in drafts]

        drafts = await _generate()
        if drafts and drafts[0].split_fallback:
            section_meta[section.section_id]["plan_failed"] = True
        min_chars = ctx.min_chars if ctx.min_chars is not None else DEFAULT_MIN_CHARS
        max_chars = ctx.max_chars if ctx.max_chars is not None else DEFAULT_MAX_CHARS
        cset = gate_candidates(
            section.section_id, drafts, chunks, min_chars=min_chars, max_chars=max_chars
        )
        # HARD 전멸 시 1회만 재생성 — n=1의 안전망이다. 캡을 1회로 두어 무한성을
        # 유지하고, 재시도도 실패하면 그대로 넘겨 게이트가 all_excluded로 표면화한다.
        if not cset.survivors and settings.write_retry_on_empty:
            emit_step(pid, "writing", f"{label} (재생성)", "started")
            retry_drafts = await _generate(base_temperature=RETRY_TEMPERATURE)
            retry_set = gate_candidates(
                section.section_id, retry_drafts, chunks, min_chars=min_chars, max_chars=max_chars
            )
            emit_step(
                pid,
                "writing",
                f"{label} (재생성)",
                "completed" if retry_set.survivors else "failed",
            )
            if retry_set.survivors:
                cset = retry_set
        if not cset.survivors:
            # 재시도까지 전멸 — 침묵하면 빈 절이 '완성' 뒤에 숨는다(2026-08-13 실사고:
            # 6.1 빈 절·7.1 토막이 completed로 마감). 실패 사실·사유를 절 meta에 남겨
            # 화면이 보여주게 하고, 완성 차단은 assemble의 structure 검사가 맡는다.
            meta = section_meta[section.section_id]
            meta["write_failed"] = True
            detail = next(
                (
                    r.detail
                    for cand in cset.candidates
                    for r in cand.report.results
                    if not r.passed and r.severity is CheckSeverity.HARD and r.detail
                ),
                None,
            )
            if detail:
                meta["fail_detail"] = detail
            logger.error(
                "write_loop.section_failed",
                project_id=str(pid),
                section=f"{section.chapter_number}.{section.section_number}",
                detail=detail,
            )
        if cset.survivors:
            # 사실 대장 적립 — 절이 확정한 값을 결정적으로 뽑아 meta에 남긴다.
            # 마커가 아직 로컬 번호인 이 시점이어야 chunk_id로 풀 수 있다(renumber 전).
            label_ref = f"{section.chapter_number}.{section.section_number}"
            entries = extract_entries(
                cset.survivors[0].draft.content,
                label_ref,
                section_meta[section.section_id].get("pool_chunk_ids", []),
            )
            section_meta[section.section_id]["ledger_entries"] = entries
            if entries:
                ledger_by_label[label_ref] = entries
        if draft_store is not None:
            # 절 완성 즉시 초안 영속화 — 편집기 미리보기가 진행 중에도 완성분을 보여준다
            survivor = cset.survivors[0].draft if cset.survivors else None
            # 지금까지 완료된 절들의 지표 스냅샷을 실어 넘긴다 — 작성 중 미리보기 배지용.
            # 스냅샷이어야 draft_store가 await하는 동안 다른 절의 mutation이 새지 않는다.
            await draft_store(state.with_section_meta(dict(section_meta)), section, survivor)
        emit_step(pid, "writing", label, "completed" if cset.survivors else "failed")
        return cset

    # 절끼리는 의존성이 없어(각자 검색→생성→게이트) 병렬이 안전하다. 상한은
    # LLM 레이트리밋 보호 — 어댑터의 429 백오프가 있지만 동시 폭주 자체를 줄인다.
    sem = asyncio.Semaphore(max(1, settings.write_section_concurrency))
    # 시스템 장애 판별 — 성공이 하나도 없는 채 연속 실패가 이 수에 닿으면 전체를 세운다.
    # (키·DB 전면 장애를 20절 내내 개별 실패로 갈아 넣지 않기 위한 하한)
    consecutive_failures = 0

    async def _fail_section(section: SectionPlan, exc: Exception, kind: str) -> SectionCandidateSet:
        """절 하나를 실패로 기록하고 나머지는 계속 — 죽은 절도 행으로 남겨 화면이 안다."""
        meta = section_meta.setdefault(section.section_id, {})
        meta["write_failed"] = True
        meta["fail_detail"] = f"{kind}: {exc}"
        emit_step(
            pid,
            "writing",
            f"본문 작성 · {section.chapter_number}.{section.section_number} {section.title}",
            "failed",
        )
        if draft_store is not None:
            # 실패 절도 행으로 남긴다(status=failed·빈 본문) — 미리보기가
            # '이 절은 비었음'을 정직하게 보여주고 재작성 진입점이 된다.
            await draft_store(state.with_section_meta(dict(section_meta)), section, None)
        return SectionCandidateSet(section_id=section.section_id)

    async def _bounded(section: SectionPlan) -> SectionCandidateSet:
        nonlocal consecutive_failures
        async with sem:
            try:
                result = await _process_section(section)
                consecutive_failures = 0
                return result
            except LLMClientError as exc:
                # 절 하나의 LLM 실패(어댑터 백오프 재시도 소진)가 나머지 절의 작업을
                # 통째로 버리게 하지 않는다 — 절만 실패로 기록하고 계속.
                logger.error(
                    "write_loop.section_llm_error",
                    project_id=str(pid),
                    section=f"{section.chapter_number}.{section.section_number}",
                    error=str(exc),
                )
                consecutive_failures += 1
                return await _fail_section(section, exc, "LLM 호출 실패")
            except cancel.RunCancelled:
                raise
            except Exception as exc:
                # 비-LLM 예외(검색·리랭커·저장 등)도 절 단위로 격리한다 — 전에는 그대로
                # 전파해 병렬 전체가 즉사했고, 트레이스백이 콘솔에만 남아 유실되면
                # 사인 불명이 됐다(2026-08-15 실측: 2장 작성 중 전체 사망, 6절 초안 유실
                # 위기). 단 연속 실패가 쌓이면 시스템 문제로 승격해 세운다.
                logger.exception(
                    "write_loop.section_error",
                    project_id=str(pid),
                    section=f"{section.chapter_number}.{section.section_number}",
                )
                consecutive_failures += 1
                if consecutive_failures >= 4:
                    # 성공 없이 4절 연속 실패 — 절 문제가 아니라 환경 문제다.
                    raise
                return await _fail_section(section, exc, "작성 중 오류")

    # builds_on 위상 정렬 — 레벨 0 배치가 전부 끝나야 레벨 1이 시작한다(의존 절이
    # 앞 절의 사실 대장을 받으려면 그 절이 완료·적립돼 있어야 한다). 의존이 하나도
    # 없으면 배치 1개 = 종전 평면 병렬 그대로다(코드 분기 없음, 조사분석 실측 0/0/0).
    exec_batches, order_warnings = builds_on_batches(state.section_plan)
    for w in order_warnings:
        logger.warning("write_loop.builds_on_order", project_id=str(pid), detail=w)
    results_by_id: dict[UUID, SectionCandidateSet] = {}
    for batch in exec_batches:
        tasks = [asyncio.ensure_future(_bounded(s)) for s in batch]
        try:
            batch_results = list(await asyncio.gather(*tasks))
        except BaseException:
            # 취소(RunCancelled·CancelledError)든 한 절의 실제 예외든, 진행 중인 나머지
            # 절을 정리하고 전파한다 — 고아 태스크가 취소 후에도 LLM을 계속 부르지 않게.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for sec, res in zip(batch, batch_results, strict=True):
            results_by_id[sec.section_id] = res
    # candidate_sets 순서 = section_plan 순서(배치가 섞어도 산출 순서는 직렬과 동일).
    candidate_sets = [results_by_id[s.section_id] for s in state.section_plan]
    # 완료 순서로 쌓인 meta를 절 순서로 재배열 — 직렬 버전과 동일한 산출을 보장한다.
    # 실패 절은 meta가 부분적일 수 있어 .get으로 읽는다(격리 경로에서 채우지만 방어).
    ordered_meta = {s.section_id: section_meta.get(s.section_id, {}) for s in state.section_plan}
    return state.with_section_candidates(candidate_sets).with_section_meta(ordered_meta)


def section_plan_payload(plan: Sequence[SectionPlan]) -> list[dict[str, object]]:
    """게이트 payload용 section_plan 직렬화 — 화면 표시용(SOURCE_POOL·QA_SELECT 공유).

    복원의 진실은 이제 projects.config["_section_plan"]이다(core.section_plan). 게이트
    payload는 사람이 그 시점에 본 것을 남기는 감사 이력이자 구버전 복원 폴백이라
    같은 모양을 유지한다.
    """
    return list(dump_section_plan(plan))


def plan_from_payload(payload: dict[str, Any]) -> list[SectionPlan]:
    """게이트 payload에서 section_plan 복원 — config 정본이 없는 옛 런의 폴백."""
    return load_section_plan(payload.get("section_plan", []))


def qa_select_payload(state: ProjectState) -> dict[str, object]:
    """QA_SELECT 게이트 payload — 섹션별 '살아남은'(HARD 통과) 후보 + soft 경고만 사람에게.

    HARD 실패 후보는 노출하지 않는다. survivors가 0이면 all_excluded=True로 표시 —
    사람이 재생성/수동편집을 결정할 신호.
    """
    sections: list[dict[str, object]] = []
    for cset in state.section_candidates:
        survivors: list[dict[str, object]] = [
            {
                "candidate_id": str(cand.candidate_id),
                "content": cand.draft.content,
                "cited_chunk_ids": [str(c) for c in cand.draft.cited_chunk_ids],
                "warnings": [{"check": w.check, "detail": w.detail} for w in cand.report.warnings],
            }
            for cand in cset.survivors
        ]
        sections.append(
            {
                "section_id": str(cset.section_id),
                "candidates": survivors,
                "all_excluded": not survivors,
            }
        )
    return {
        "message": "절별 생성 초안을 검토하세요. (정적검사 통과분만 표시 — 승인하면 조립 시작)",
        "section_plan": section_plan_payload(state.section_plan),
        "sections": sections,
    }


def rehydrate_from_payload(state: ProjectState, payload: dict[str, Any]) -> ProjectState:
    """QA_SELECT review payload에서 section_plan·section_candidates를 복원.

    resume는 별도 프로세스라 in-memory ProjectState가 사라진다 — 게이트 payload에
    실어둔 값으로 되살린다. 후보는 survivors만 복원되며(payload에 그것만 있음), 이는
    선택 대상과 일치한다. report는 이미 게이트를 통과한 값이라 빈 채로 둔다.
    """
    plan = plan_from_payload(payload)
    candidate_sets: list[SectionCandidateSet] = []
    for sec in payload.get("sections", []):
        section_id = UUID(sec["section_id"])
        candidates = [
            SectionCandidate(
                candidate_id=UUID(c["candidate_id"]),
                draft=SectionDraft(
                    section_id=section_id,
                    content=c["content"],
                    cited_chunk_ids=[UUID(x) for x in c["cited_chunk_ids"]],
                ),
            )
            for c in sec["candidates"]
        ]
        candidate_sets.append(SectionCandidateSet(section_id=section_id, candidates=candidates))
    return state.with_section_plan(plan).with_section_candidates(candidate_sets)


def auto_select_survivors(state: ProjectState) -> ProjectState:
    """절마다 첫 생존 후보를 자동 채택한다 — QA 게이트 제거(2026-08-07) 후의 기본 경로.

    n=1이라 '고르기'가 없고, 검토·수정은 통합 화면에서 완성 후에 한다. 전멸 절은
    선택 없이 남아 assemble의 structure 검사가 누락으로 표면화한다(기존 계약 유지).
    """
    updated = state
    for cset in state.section_candidates:
        if cset.survivors:
            updated = updated.record_selection(cset.section_id, cset.survivors[0].candidate_id)
    return updated


def overlay_working_copy(
    state: ProjectState, working: dict[UUID, tuple[str, list[UUID]]]
) -> ProjectState:
    """sections 행(작업 사본)을 후보 draft 위에 덮어쓴다 — 행 내용이 항상 우선.

    통합 검토 화면(2026-08-07)에서 사람은 직접 편집·AI 재작성으로 절을 고친다 —
    그 결과는 sections 테이블에만 있다. resume 시 행 내용이 인메모리/payload 후보보다
    우선해야 편집이 조립에 살아남는다. 편집이 없었으면 행 == 후보라 멱등이다.
    후보가 없는 절(전멸, 또는 QA 게이트 제거 후 크래시 복구로 후보 자체가 소실)에
    행 내용이 있으면 합성 후보로 승격+선택 기록해 조립에 포함시킨다 — plan 기준
    순회라 sections 행만으로도 조립 가능한 상태를 재구성할 수 있다.
    """
    if not working and not state.section_candidates:
        return state
    by_sec = {cs.section_id: cs for cs in state.section_candidates}
    updated = state
    new_sets: list[SectionCandidateSet] = []
    for plan in state.section_plan:
        cset = by_sec.pop(plan.section_id, None)
        row = working.get(plan.section_id)
        if row is None or not row[0].strip():
            if cset is not None:
                new_sets.append(cset)
            continue
        content, cited = row
        if cset is not None and cset.candidates:
            new_sets.append(
                cset.model_copy(
                    update={
                        "candidates": [
                            c.model_copy(
                                update={
                                    "draft": c.draft.model_copy(
                                        update={"content": content, "cited_chunk_ids": cited}
                                    )
                                }
                            )
                            for c in cset.candidates
                        ]
                    }
                )
            )
        else:
            cand = SectionCandidate(
                draft=SectionDraft(
                    section_id=plan.section_id, content=content, cited_chunk_ids=cited
                )
            )
            new_sets.append(SectionCandidateSet(section_id=plan.section_id, candidates=[cand]))
            updated = updated.record_selection(plan.section_id, cand.candidate_id)
    # plan 밖 후보는 그대로 유지(방어 — 정상 흐름에선 발생하지 않는다)
    new_sets.extend(by_sec.values())
    return updated.with_section_candidates(new_sets)


def apply_selection(state: ProjectState, selections: dict[str, str]) -> ProjectState:
    """사람 결정(section_id→candidate_id, JSON 문자열 UUID)을 state에 반영."""
    updated = state
    for section_id_str, candidate_id_str in selections.items():
        updated = updated.record_selection(UUID(section_id_str), UUID(candidate_id_str))
    return updated


def check_assembled(state: ProjectState) -> tuple[list[SectionDraft], GateResult]:
    """선택된 draft를 조립하고 보고서 레벨 정적검사(structure_complete)를 실행.

    누락 섹션이 있으면 GateResult.passed=False — 최종 게이트에서 사람에게 되돌릴 신호.
    """
    drafts = state.selected_drafts()
    result = check_structure_complete(drafts, state.section_plan)
    return drafts, result
