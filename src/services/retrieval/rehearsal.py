"""검색 리허설 — 색인 직후, 작성과 같은 검색을 절마다 미리 돌려 근거 충분성을 판정한다.

작성이 시작되고 나서야 "이 절은 자료가 없다"를 아는 구조는 비싸다 — 이미 LLM 비용이
나갔고, scale_for_evidence가 분량을 깎는 것 말고는 할 수 있는 게 없다. 리허설은 그
판정을 색인 직후로 당겨, 공백 절이 있으면 자료 게이트를 다시 열어 사람이 고칠 기회를
만든다(작성 전이라 비용이 들지 않은 시점).

3밴드 — 경계는 절대치가 아니라 절 목표 분량에서 유도한다(scale_for_evidence와 같은
상수 CHARS_PER_EVIDENCE를 쓰므로 "리허설 ok = 작성에서 분량 안 깎임"이 성립):

- ok:    floor 통과 근거 수 ≥ 필요 근거(min_chars / 750) — 그대로 진행
- hyde:  공백은 아니지만 부족 — HyDE 재검색 1회로 보강 시도(더 나은 쪽 채택)
- empty: 하한(max(3, 필요/3)) 미만 — 자료 게이트 재개방 사유

리허설↔작성 동근거 계약: 리허설이 본 청크를 section_rehearsals에 영속하고, 작성은
같은 index_version이면 그 목록을 재검색 없이 그대로 쓴다. index_version은 청크가
변하는 사건(색인·0청크 자동 제외·채택 토글)마다 증가한다 — 버전이 갈리면 캐시는
자동 무효이고 작성이 실검색으로 폴백한다(리허설 판정도 그 시점 기준으론 낡은 것).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.types import RetrievedChunk, SectionPlan
from src.services.generation.writer_context import CHARS_PER_EVIDENCE

if TYPE_CHECKING:
    from src.services.retrieval.section import SectionRetriever

logger = structlog.get_logger(__name__)

# 목표 분량이 없는 절(볼륨 미지정)의 필요 근거 기본값 — 2026-08-15 합의 시작값
# (≥12 진행 / 4~11 HyDE / <4 공백)과 같은 스케일. min_chars 9,000자와 등가.
DEFAULT_NEEDED = 12
# 하한의 바닥 — 필요 근거가 아무리 작아도 이 밑으로는 공백 판정 기준을 내리지 않는다
# (select_relevant의 _MIN_KEEP과 같은 3 — 근거 3개 미만이면 절을 쓸 수 없다는 경험칙).
_EMPTY_FLOOR_MIN = 3

BAND_OK = "ok"
BAND_HYDE = "hyde"
BAND_EMPTY = "empty"
# 리허설 없이 작성 시점에 실검색으로 채워진 행 — 밴드 판정이 아니라 동근거 기록용.
BAND_LIVE = "live"


def plan_fingerprint(plan: SectionPlan) -> str:
    """검색에 영향을 주는 계획 필드의 지문 — 리허설 캐시의 내용 무효화 열쇠.

    절 id가 안정화되면서(2026-08-21) "목차 편집 = id 재발급 = 캐시 자연 미스"라는
    가정이 깨졌다. 같은 id의 절이라도 제목·장 제목·방향·핵심 포인트·검색 질의·
    에이전트가 바뀌면 검색 결과가 달라지므로 지문으로 잡는다. 번호는 넣지 않는다 —
    순서만 바뀐 절은 같은 근거를 그대로 써도 된다.
    """
    payload = json.dumps(
        [
            plan.title,
            plan.chapter_title,
            plan.direction,
            list(plan.key_points),
            list(plan.search_queries),
            list(plan.analysts),
        ],
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def needed_evidence(min_chars: int | None) -> int:
    """절 목표 분량 → 필요 근거 수. scale_for_evidence의 역산과 같은 상수를 쓴다."""
    if not min_chars:
        return DEFAULT_NEEDED
    return max(1, math.ceil(min_chars / CHARS_PER_EVIDENCE))


def empty_floor(needed: int) -> int:
    """공백 판정 하한 — 필요 근거의 1/3(바닥 3). 시작값 밴드(<4 공백)와 등가."""
    return max(_EMPTY_FLOOR_MIN, needed // 3)


def classify(n_citable: int, needed: int) -> str:
    """floor 통과 근거 수 → 밴드."""
    if n_citable >= needed:
        return BAND_OK
    if n_citable < empty_floor(needed):
        return BAND_EMPTY
    return BAND_HYDE


async def current_index_version(project_id: UUID) -> int:
    from src.db.models.project import Project
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        version = (
            await session.execute(select(Project.index_version).where(Project.id == project_id))
        ).scalar_one_or_none()
    return int(version or 0)


async def bump_index_version(project_id: UUID) -> int:
    """청크가 변했다 — 리허설 캐시를 전부 무효화한다(원자적 +1).

    호출 지점은 검색 결과가 달라질 수 있는 사건뿐이다: 색인 실행(청크 생성),
    0청크 자동 제외, 채택 토글. 이보다 자주 부르면 캐시가 무의미해지고, 덜 부르면
    리허설과 작성이 다른 근거를 본다.
    """
    from src.db.models.project import Project
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        version = (
            await session.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(index_version=Project.index_version + 1)
                .returning(Project.index_version)
            )
        ).scalar_one_or_none()
        await session.commit()
    logger.info("index_version.bumped", project_id=str(project_id), version=version)
    return int(version or 0)


async def store_rehearsal(
    project_id: UUID,
    section_id: UUID,
    *,
    index_version: int,
    chunks: list[RetrievedChunk],
    band: str,
    floor_passed: int,
    needed: int,
    hyde_used: bool = False,
    warnings: dict | None = None,
    plan_hash: str = "",
) -> None:
    """리허설 결과 upsert(절당 1행) — 요약(is_summary)은 싣지 않는다(인용 불가 배경)."""
    from src.db.models.section_rehearsal import SectionRehearsal
    from src.db.session import async_session_maker

    payload = [{"id": str(c.chunk_id), "score": c.score} for c in chunks if not c.is_summary]
    stmt = pg_insert(SectionRehearsal).values(
        project_id=project_id,
        section_id=section_id,
        index_version=index_version,
        plan_hash=plan_hash,
        band=band,
        floor_passed=floor_passed,
        needed=needed,
        hyde_used=hyde_used,
        chunks=payload,
        warnings=warnings or {},
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SectionRehearsal.project_id, SectionRehearsal.section_id],
        set_={
            "index_version": stmt.excluded.index_version,
            "plan_hash": stmt.excluded.plan_hash,
            "band": stmt.excluded.band,
            "floor_passed": stmt.excluded.floor_passed,
            "needed": stmt.excluded.needed,
            "hyde_used": stmt.excluded.hyde_used,
            "chunks": stmt.excluded.chunks,
            "warnings": stmt.excluded.warnings,
        },
    )
    async with async_session_maker() as session:
        await session.execute(stmt)
        await session.commit()


async def load_rehearsal(
    project_id: UUID, section_id: UUID, index_version: int, plan_hash: str = ""
) -> list[RetrievedChunk] | None:
    """리허설이 본 근거를 그대로 복원. 버전·계획 지문 불일치·청크 유실이면 None(실검색 폴백).

    청크 하나라도 사라졌으면(자료 삭제 등) 부분 목록을 돌려주지 않고 통째로
    무효 처리한다 — 반쪽 근거로 쓰는 것보다 다시 검색하는 쪽이 안전하다.
    plan_hash를 넘기면 저장 시점 지문과 대조한다 — 절 id가 안정화된 뒤로는 목차
    편집이 id를 안 바꾸므로, 계획 내용 변경은 지문으로만 잡을 수 있다.
    """
    from src.db.models.chunk import Chunk
    from src.db.models.section_rehearsal import SectionRehearsal
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        row = await session.get(SectionRehearsal, (project_id, section_id))
        if row is None or row.index_version != index_version:
            return None
        if plan_hash and row.plan_hash and row.plan_hash != plan_hash:
            return None
        entries = [e for e in (row.chunks or []) if isinstance(e, dict) and e.get("id")]
        if not entries:
            return []
        ids = [UUID(str(e["id"])) for e in entries]
        chunk_rows = (await session.execute(select(Chunk).where(Chunk.id.in_(ids)))).scalars().all()
    by_id = {c.id: c for c in chunk_rows}
    if len(by_id) != len(ids):
        logger.warning(
            "rehearsal.cache_chunks_missing",
            project_id=str(project_id),
            section_id=str(section_id),
            missing=len(ids) - len(by_id),
        )
        return None
    restored: list[RetrievedChunk] = []
    for e in entries:
        c = by_id[UUID(str(e["id"]))]
        header = c.metadata_.get("header_path") if isinstance(c.metadata_, dict) else None
        restored.append(
            RetrievedChunk(
                chunk_id=c.id,
                source_id=c.source_id,
                content=c.content,
                score=float(e.get("score") or 0.0),
                header_path=[str(h) for h in header] if isinstance(header, list) else [],
            )
        )
    return restored


async def load_rehearsal_bands(project_id: UUID, index_version: int) -> dict[UUID, dict]:
    """현재 버전의 절별 리허설 요약(band·floor_passed·needed·warnings) — 게이트 payload용."""
    from src.db.models.section_rehearsal import SectionRehearsal
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(SectionRehearsal).where(
                        SectionRehearsal.project_id == project_id,
                        SectionRehearsal.index_version == index_version,
                    )
                )
            )
            .scalars()
            .all()
        )
    return {
        r.section_id: {
            "band": r.band,
            "floor_passed": r.floor_passed,
            "needed": r.needed,
            "hyde_used": r.hyde_used,
            "warnings": dict(r.warnings or {}),
        }
        for r in rows
    }


def make_cached_retriever(
    inner: SectionRetriever,
    project_id: UUID,
    index_version: int,
) -> SectionRetriever:
    """리허설 캐시 우선 retriever — 작성이 리허설과 같은 근거를 보게 하는 계약.

    캐시 미스(리허설 없던 옛 런·버전 뒤바뀜·청크 유실)는 실검색으로 폴백하고 결과를
    BAND_LIVE로 기록해 둔다 — 같은 실행 안에서 재시도·크래시 재개가 또 다른 근거를
    보지 않게.
    """

    async def _retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        cached = await load_rehearsal(
            project_id, section.section_id, index_version, plan_fingerprint(section)
        )
        if cached is not None:
            from src.services.retrieval.section import _with_source_titles

            logger.info(
                "rehearsal.cache_hit",
                project_id=str(project_id),
                section=f"{section.chapter_number}.{section.section_number}",
                n_chunks=len(cached),
            )
            return await _with_source_titles(cached)
        chunks = await inner(section)
        citable = [c for c in chunks if not c.is_summary]
        try:
            await store_rehearsal(
                project_id,
                section.section_id,
                index_version=index_version,
                chunks=citable,
                band=BAND_LIVE,
                floor_passed=len(citable),
                needed=0,
                plan_hash=plan_fingerprint(section),
            )
        except Exception:
            # 기록 실패가 작성을 막으면 안 된다 — 동근거 보장만 약해질 뿐.
            logger.warning("rehearsal.live_store_failed", project_id=str(project_id), exc_info=True)
        return chunks

    return _retrieve


# ── 절쌍 근거팩 겹침(실측) ──────────────────────────────────────────────
# 질의 '문자열' 중복 경고는 문구가 다른데 결과가 수렴하는 병리를 통과시킨다
# (2026-09-02 철강 실측: 1.1↔5.1은 질의가 달랐지만 같은 K-스틸법 자료를 받아
# 축자 재탕 14건). 7차 실측에서 쌍둥이 절 자카드 0.59~0.62가 재탕의 직접 원인 —
# 리허설이 어차피 검색을 돌리므로 그 결과로 겹침을 실측해 게이트에 올린다.
PACK_OVERLAP_THRESHOLD = 0.45
_MAX_OVERLAP_PAIRS = 8


def _label_key(label: str) -> tuple[int, int]:
    a, _, b = label.partition(".")
    try:
        return (int(a), int(b or 0))
    except ValueError:
        return (0, 0)


def overlap_pairs_from_packs(packs: dict[str, set[str]]) -> list[dict]:
    """절 라벨 → 청크 id 집합에서 겹침 쌍을 자카드 내림차순으로. 순수 함수."""
    labels = sorted((lab for lab, s in packs.items() if s), key=_label_key)
    out: list[dict] = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = packs[labels[i]], packs[labels[j]]
            inter = a & b
            if not inter:
                continue
            jac = len(inter) / len(a | b)
            if jac >= PACK_OVERLAP_THRESHOLD:
                out.append(
                    {
                        "sections": [labels[i], labels[j]],
                        "jaccard": round(jac, 2),
                        "shared_chunk_ids": sorted(inter)[:40],
                    }
                )
    out.sort(key=lambda x: -x["jaccard"])
    return out[:_MAX_OVERLAP_PAIRS]


async def attach_shared_sources(overlaps: list[dict]) -> list[dict]:
    """겹침 쌍의 공유 청크가 어느 자료인지 제목을 붙인다(쌍당 상위 2개).

    사람이 게이트에서 "무엇이 겹치는가"를 자료명으로 봐야 소유권 분담을 판단할 수
    있다 — 청크 id 목록은 판단 재료가 아니다.
    """
    if not overlaps:
        return overlaps
    from collections import Counter

    from src.db.models.chunk import Chunk
    from src.db.models.project_source import ProjectSource
    from src.db.session import async_session_maker

    all_ids = {cid for o in overlaps for cid in o.get("shared_chunk_ids", [])}
    if not all_ids:
        return overlaps
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(Chunk.id, ProjectSource.title)
                .join(ProjectSource, ProjectSource.id == Chunk.source_id)
                .where(Chunk.id.in_([UUID(x) for x in all_ids]))
            )
        ).all()
    title_by_chunk = {str(cid): (title or "") for cid, title in rows}
    for o in overlaps:
        counts = Counter(title_by_chunk.get(cid, "") for cid in o.get("shared_chunk_ids", []))
        counts.pop("", None)
        o["shared_sources"] = [t[:60] for t, _n in counts.most_common(2)]
        o.pop("shared_chunk_ids", None)  # 화면·브리프에 id 목록은 잡음이다
    return overlaps


async def stored_pack_overlaps(project_id: UUID, label_by_sid: dict[str, str]) -> list[dict]:
    """영속된 리허설 팩으로 겹침을 계산한다 — 게이트 재개방·브리프 표시용.

    리허설 전(첫 게이트)에는 행이 없어 빈 목록 — 색인 후 다시 열린 게이트부터
    실측이 실린다.
    """
    from src.db.models.section_rehearsal import SectionRehearsal
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(SectionRehearsal).where(SectionRehearsal.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        return []
    latest = max(r.index_version for r in rows)
    packs: dict[str, set[str]] = {}
    for r in rows:
        if r.index_version != latest:
            continue
        label = label_by_sid.get(str(r.section_id))
        if not label:
            continue
        packs[label] = {str(c.get("id")) for c in (r.chunks or []) if c.get("id")}
    return await attach_shared_sources(overlap_pairs_from_packs(packs))
