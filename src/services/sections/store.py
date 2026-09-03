"""완료 섹션 영구 저장 — assemble 시점에 selected_drafts를 sections 테이블에 적재.

ProjectState는 인메모리 작업사본이라 실행이 끝나면 사라진다. 이 모듈이 그 순간의
확정 본문을 정규 테이블로 옮겨, 사후 조회·편집(GET/PATCH /sections)의 유일한 원천이 된다.
프로젝트당 전량 교체(delete-all → insert) — 재실행 시 stale 행이 남지 않는다.
"""

from __future__ import annotations

import structlog
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.state import ProjectState
from src.core.types import SectionCandidateSet, SectionPlan
from src.db.models.section import Section
from src.db.session import open_session
from src.prompts import load_preset
from src.services.sections.drift import content_fingerprint

logger = structlog.get_logger(__name__)


def _chapter_titles(state: ProjectState) -> dict[int, str]:
    """chapter_number → 챕터 제목. plan이 실어 온 값이 진실이다.

    전에는 프리셋 파일(load_preset)에서 위치로 가져왔는데, 그게 이미 데이터를 망가뜨리고
    있었다(2026-08-14 운영 DB 실측):

    - 프리셋 없는 프로젝트는 전부 'N장'으로 저장됐다. 탄소규제 런의 sections는
      '1장~4장'인데 config.outline에는 '글로벌 RE100'·'EU CBAM'…이 그대로 있었다.
    - 프리셋을 쓰되 장을 더한 프로젝트는 6장에 프리셋의 6번째 제목이 붙어 **틀린 제목**이
      저장됐다. 폴백보다 나쁘다 — 그럴듯해서 아무도 못 알아챈다.
    - 개인 프리셋(u:<uuid>)은 load_preset이 KeyError라 통째로 'N장'이었다.

    사용자가 목차 화면에서 고친 장 제목이 저장에 반영되지 않던 것이라, 자료 사용 통계의
    장 라벨도 같이 틀렸다(sections.chapter_title이 그 원천).

    프리셋 폴백은 plan에 chapter_title이 없던 시절 시작된 런을 위해 남긴다.
    """
    titles: dict[int, str] = {}
    for plan in state.section_plan:
        if plan.chapter_title:
            titles.setdefault(plan.chapter_number, plan.chapter_title)
    if state.preset:
        try:
            preset = load_preset(state.preset)
            for i, ch in enumerate(preset.chapters, start=1):
                titles.setdefault(i, ch.title)
        except KeyError:
            pass
    for plan in state.section_plan:
        titles.setdefault(plan.chapter_number, f"{plan.chapter_number}장")
    return titles


def _qa_status(section_id, selections, cand_sets: list[SectionCandidateSet]) -> str:
    """선택된 후보의 정적검사 결과 → qa_status. soft 경고 있으면 failed, 없으면 passed."""
    chosen = selections.get(section_id)
    if chosen is None:
        return "pending"
    for cset in cand_sets:
        if cset.section_id != section_id:
            continue
        for cand in cset.candidates:
            if cand.candidate_id == chosen:
                return "failed" if cand.report.warnings else "passed"
    return "pending"


def _rows(state: ProjectState) -> list[Section]:
    drafts = {d.section_id: d for d in state.selected_drafts()}
    chapter_titles = _chapter_titles(state)
    rows: list[Section] = []
    for plan in state.section_plan:
        draft = drafts.get(plan.section_id)
        meta = dict(state.section_meta.get(plan.section_id) or {})
        if draft is None:
            # 작성 실패 절(meta.write_failed)은 failed로 보존한다 — 전량 교체가
            # 증분 저장의 failed 행을 pending으로 되돌리면, 화면이 '아직 안 쓴 절'과
            # '쓰다 실패한 절'을 구분하지 못한다(2026-08-13 완성 게이트와 한 세트).
            status = "failed" if meta.get("write_failed") else "pending"
        else:
            status = "completed"
        rows.append(
            Section(
                id=plan.section_id,
                project_id=state.project_id,
                chapter_number=plan.chapter_number,
                section_number=plan.section_number,
                chapter_title=chapter_titles.get(plan.chapter_number, f"{plan.chapter_number}장"),
                title=plan.title,
                level=2,
                content=draft.content if draft is not None else "",
                source_ids=list(draft.cited_chunk_ids) if draft is not None else [],
                # 이 본문이 어떤 계약으로 쓰였는지 — 나중에 목차를 고치면 이 지문이
                # 달라져 "미반영"으로 드러난다(0047). 본문 없는 절은 비워 둔다.
                plan_hash=content_fingerprint(plan) if draft is not None else "",
                meta=meta,
                qa_status=_qa_status(
                    plan.section_id, state.section_selections, state.section_candidates
                ),
                status=status,
            )
        )
    return rows


async def persist_sections(state: ProjectState) -> None:
    """selected_drafts를 sections 테이블로 전량 교체 저장(프로젝트 스코프).

    요청 밖 경로라 자체 세션을 열고(=opener) 직접 커밋한다(session.py 규칙).
    """
    rows = _rows(state)
    async with open_session() as session:
        # 전량 교체 전에 기존 meta를 읽어 둔다 — resume 조립은 state에 지표가 없어
        # 그냥 덮으면 작성 때 기록한 '자료 부족' 플래그가 사라진다.
        prior = {
            row_id: meta
            for row_id, meta in (
                await session.execute(
                    select(Section.id, Section.meta).where(Section.project_id == state.project_id)
                )
            ).all()
        }
        for row in rows:
            if not row.meta and prior.get(row.id):
                row.meta = dict(prior[row.id])
        await session.execute(delete(Section).where(Section.project_id == state.project_id))
        session.add_all(rows)
        await session.commit()
    logger.info("sections.persisted", project_id=str(state.project_id), n=len(rows))


async def clear_project_sections(project_id) -> None:
    """write 시작 시 이전 런 잔재 제거 — 증분 초안이 옛 완성본과 섞이지 않게."""
    async with open_session() as session:
        await session.execute(delete(Section).where(Section.project_id == project_id))
        await session.commit()


async def persist_draft_section(state: ProjectState, plan, draft) -> None:
    """작성 진행 중 절 1개 초안을 업서트(status=writing) — 미리보기 증분 표시용.

    확정본이 아니다: 인용 [n]은 절 로컬 번호이고 QA 승인·조립(persist_sections)이
    전량 교체한다. 미리보기는 부가 기능이라 실패해도 작성 루프를 죽이지 않는다
    (삼키고 경고만). draft=None(정적 게이트 전멸)은 status=failed 빈 본문으로 남겨
    편집 화면이 '이 절은 비었음'을 정직하게 보여주게 한다.
    """
    try:
        chapter_titles = _chapter_titles(state)
        async with open_session() as session:
            # 같은 절(id)의 잔재(재시도·이전 부분 실행)와 같은 위치를 차지한 낡은
            # 행(목차 재정렬 잔재)을 함께 걷어내고 새 초안으로 교체 — 정체성은 id,
            # 위치는 표시값이라 둘 다 청소해야 uq_section_pos 충돌이 없다.
            await session.execute(
                delete(Section).where(
                    Section.project_id == state.project_id,
                    or_(
                        Section.id == plan.section_id,
                        and_(
                            Section.chapter_number == plan.chapter_number,
                            Section.section_number == plan.section_number,
                        ),
                    ),
                )
            )
            session.add(
                Section(
                    id=plan.section_id,
                    project_id=state.project_id,
                    chapter_number=plan.chapter_number,
                    section_number=plan.section_number,
                    chapter_title=chapter_titles.get(
                        plan.chapter_number, f"{plan.chapter_number}장"
                    ),
                    title=plan.title,
                    level=2,
                    content=draft.content if draft is not None else "",
                    source_ids=list(draft.cited_chunk_ids) if draft is not None else [],
                    plan_hash=content_fingerprint(plan) if draft is not None else "",
                    meta=dict(state.section_meta.get(plan.section_id) or {}),
                    qa_status="pending",
                    status="writing" if draft is not None else "failed",
                )
            )
            await session.commit()
    except Exception:
        logger.warning(
            "sections.draft_persist_failed",
            project_id=str(state.project_id),
            section=f"{plan.chapter_number}.{plan.section_number}",
            exc_info=True,
        )


def _relabel_meta(meta: dict, new_label: str) -> dict:
    """행 meta 안의 절 라벨(사실 대장 section_ref·서사 요약 section)을 새 번호로.

    라벨은 이 절 자신을 가리키는 값이라(적립이 자기 meta에 쌓는다) 소유 행의 현재
    번호가 곧 진실이다 — 안 고치면 재개 복원(write_loop)과 조인 검출(ledger)이
    옛 번호로 매칭해 주입·검출이 어긋난다.
    """
    out = dict(meta)
    entries = out.get("ledger_entries")
    if isinstance(entries, list):
        out["ledger_entries"] = [
            {**e, "section_ref": new_label} if isinstance(e, dict) else e for e in entries
        ]
    chain = out.get("chain_summary")
    if isinstance(chain, dict) and chain.get("section"):
        out["chain_summary"] = {**chain, "section": new_label}
    return out


async def sync_rows_to_plan(session: AsyncSession, project_id, plan: list[SectionPlan]) -> None:
    """목차 변경 후 절 행을 plan(절 id 기준)에 맞춰 재정렬한다 — API 세션 안에서.

    같은 id의 행은 본문·근거·상태를 지키고 번호·제목만 새 목차로 옮긴다. plan에서
    사라진 id의 행은 지운다(사용자가 그 절을 목차에서 뺐다). plan에 새로 생긴 절은
    빈 pending 행으로 만들어 미리보기 트리에 바로 보이게 한다. 행이 하나도 없으면
    아무것도 안 한다(아직 작성 전 — write가 처음부터 만든다).

    전량 삭제 후 재삽입인 이유: 번호 UPDATE는 자리 맞바꿈에서 uq_section_pos와
    충돌한다(행 단위 검사). persist_sections와 같은 패턴이다.
    """
    rows = (
        await session.execute(
            select(
                Section.id,
                Section.content,
                Section.source_ids,
                Section.plan_hash,
                Section.locked,
                Section.meta,
                Section.qa_status,
                Section.status,
                Section.created_at,
            ).where(Section.project_id == project_id)
        )
    ).all()
    if not rows:
        return
    by_id = {r.id: r for r in rows}
    new_rows: list[Section] = []
    for p in plan:
        prev = by_id.get(p.section_id)
        label = f"{p.chapter_number}.{p.section_number}"
        chapter_title = p.chapter_title or f"{p.chapter_number}장"
        if prev is None:
            new_rows.append(
                Section(
                    id=p.section_id,
                    project_id=project_id,
                    chapter_number=p.chapter_number,
                    section_number=p.section_number,
                    chapter_title=chapter_title,
                    title=p.title,
                    level=2,
                    content="",
                    source_ids=[],
                    meta={},
                    qa_status="pending",
                    status="pending",
                )
            )
            continue
        new_rows.append(
            Section(
                id=prev.id,
                project_id=project_id,
                chapter_number=p.chapter_number,
                section_number=p.section_number,
                chapter_title=chapter_title,
                title=p.title,
                level=2,
                content=prev.content,
                source_ids=list(prev.source_ids or []),
                # 지문은 **그대로 옮긴다** — 여기서 새 계획으로 다시 계산하면 방금
                # 사람이 고친 목차가 곧바로 '반영됨'으로 위장돼 미반영 판정이 죽는다.
                plan_hash=prev.plan_hash,
                # 잠금도 그대로 옮긴다 — 안 옮기면 목차를 한 줄만 고쳐도 잠금이 전부
                # 풀려, 잠금이 막으려던 사고(묶음 재작성이 손본 절을 덮음)가 그대로
                # 난다. 전량 삭제·재삽입 방식이라 새 칸을 늘릴 때마다 여기에 와야 한다.
                locked=prev.locked,
                meta=_relabel_meta(dict(prev.meta or {}), label),
                qa_status=prev.qa_status,
                status=prev.status,
                created_at=prev.created_at,
            )
        )
    await session.execute(delete(Section).where(Section.project_id == project_id))
    await session.flush()
    session.add_all(new_rows)
    await session.flush()
    dropped = len(rows) - sum(1 for p in plan if p.section_id in by_id)
    logger.info(
        "sections.synced_to_plan",
        project_id=str(project_id),
        n_rows=len(new_rows),
        n_dropped=max(dropped, 0),
    )
