"""근거 기반 PM 경고 — 저장된 본문과 근거를 대조해 결정적으로 뽑는 경고.

PM 검증(pm_verify)은 챕터당 1콜로 문서 횡단 문제(수치 충돌·용어 불일치)를 본다. 그건
LLM이라야 보이는 축이다. 반대로 "이 문장이 인용한 근거에 그 내용이 없다"는 코드로 셀 수
있고, 세는 편이 낫다 — 매번 같은 답이 나오고 비용이 0이며, 재검증을 눌러도 흔들리지 않는다.

경고는 절이 아니라 사람에게 보내는 신호다. 그래서 문장 하나하나를 다 올리지 않고 절 단위로
묶어 "몇 건, 예시 하나"로 보낸다 — 35절짜리 보고서에서 문장마다 경고가 뜨면 아무도 안 읽는다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select

from src.core.config import settings
from src.db.models.chunk import Chunk
from src.db.models.section import Section
from src.db.session import async_session_maker
from src.services.qa.alignment import ClaimAlignment, align_section
from src.services.qa.claim_verify import verify_claims
from src.services.sections.evidence import marker_chunk_ids

logger = structlog.get_logger(__name__)

# 절 하나에서 이 개수를 넘으면 "많다"고만 알린다 — 목록 나열은 화면 몫이다.
_SAMPLE_CHARS = 40
# 무근거 주장 경고 기준 — 정적 게이트(check_uncited_claims)와 같은 눈금을 쓴다.
_UNCITED_MIN = 3
_UNCITED_RATIO = 0.5
# 근거 불일치 기준 — 의역이면 겹침이 낮게 나오므로 한두 건으로는 경고하지 않는다.
_UNMATCHED_MIN = 3
_UNMATCHED_RATIO = 0.2


def _finding(
    chapter: int, section_ref: str, severity: str, category: str, detail: str
) -> dict[str, Any]:
    return {
        "chapter_number": chapter,
        "severity": severity,
        "category": category,
        "section_ref": section_ref,
        "detail": detail,
    }


def claims_for_section(
    row: Section, chunk_texts: dict[UUID, str], *, renumbered: bool
) -> tuple[list[ClaimAlignment], bool]:
    """절 본문을 주장 단위로 쪼개 근거와 맞춰본다. (주장들, 대조 가능 여부)

    마커는 있는데 청크까지 못 풀면(기록 없는 옛 절) 근거 대조는 성립하지 않는다.
    그때도 "근거 표기가 아예 없는 주장"은 셀 수 있다 — 매핑이 필요 없는 판정이라서다.
    둘을 뭉뚱그려 건너뛰면 인용이 하나도 없는 절이 조용히 통과한다(2026-08-11).
    """
    content = row.content or ""
    mapping, _ = marker_chunk_ids(
        content, list(row.source_ids or []), row.meta, renumbered=renumbered
    )
    return align_section(content, chunk_texts, mapping), bool(mapping)


def suspicious_indices(claims: list[ClaimAlignment]) -> list[int]:
    """LLM 판정으로 넘길 후보 — 겹침이 낮거나 근거에 없는 수치를 문 문장.

    겹침이 낮다는 건 '틀렸다'가 아니라 '어휘로는 확인 못 했다'는 뜻이다. 의역이면
    여기 걸리는 게 정상이라, 이 목록을 그대로 경고로 쓰면 안 되고 판정을 한 번 더 받는다.
    """
    return [
        i
        for i, c in enumerate(claims)
        if c.numbers and (c.status in ("weak", "unmatched") or c.ungrounded)
    ]


def findings_from_claims(
    row: Section,
    claims: list[ClaimAlignment],
    *,
    comparable: bool,
    supported: set[int] | None = None,
    refuted: set[int] | None = None,
) -> list[dict[str, Any]]:
    """주장별 대조 결과 → 절 단위 경고 행 (순수 함수 — 테스트 대상).

    supported/refuted는 근거 동봉 판정(claim_verify)의 결과다. 판정을 받은 문장은
    겹침 점수 대신 그 판정을 따른다 — 뒷받침된다고 나오면 경고에서 빼고, 근거에 없다고
    나오면 겹침이 높아도 경고한다.
    """
    ref = f"{row.chapter_number}.{row.section_number}"
    if not claims:
        return []
    supported = supported or set()
    refuted = refuted or set()
    verified = bool(supported or refuted)

    out: list[dict[str, Any]] = []
    unmatched = [
        c
        for i, c in enumerate(claims)
        if i not in supported and (i in refuted or c.status == "unmatched")
    ]
    # 판정을 받았으면 1건도 유효한 신호다. 겹침만으로는 의역이 섞이므로 비중을 본다.
    enough = (
        len(unmatched) >= 1
        if verified
        else (len(unmatched) >= _UNMATCHED_MIN and len(unmatched) / len(claims) >= _UNMATCHED_RATIO)
    )
    if comparable and unmatched and enough:
        sample = unmatched[0].claim[:_SAMPLE_CHARS]
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "근거 불일치",
                f"인용 표기는 있으나 그 근거에서 확인되지 않는 문장 {len(unmatched)}건"
                f' (예: "{sample}…")',
            )
        )

    uncited = [c for c in claims if c.status == "uncited"]
    ratio = len(uncited) / len(claims)
    if len(uncited) >= _UNCITED_MIN and ratio > _UNCITED_RATIO:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "무근거 주장",
                f"근거 표기가 없는 주장 {len(uncited)}건 ({ratio:.0%})"
                f' (예: "{uncited[0].claim[:_SAMPLE_CHARS]}…")',
            )
        )

    numbers: list[str] = []
    for i, claim in enumerate(claims) if comparable else []:
        if i in supported:
            continue  # 근거로 뒷받침된다고 판정된 문장의 수치는 창작이 아니다
        for token in claim.ungrounded:
            if token not in numbers:
                numbers.append(token)
    if numbers:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "critical",
                "무근거 수치",
                f"인용한 근거에 없는 수치 {len(numbers)}건: {', '.join(numbers[:5])}"
                + (" …" if len(numbers) > 5 else ""),
            )
        )
    return out


def findings_for_section(
    row: Section, chunk_texts: dict[UUID, str], *, renumbered: bool
) -> list[dict[str, Any]]:
    """LLM 판정 없이 한 절을 대조한다 — 겹침 판정만 쓰는 경로."""
    claims, comparable = claims_for_section(row, chunk_texts, renumbered=renumbered)
    return findings_from_claims(row, claims, comparable=comparable)


async def evidence_findings(
    project_id: UUID,
    *,
    renumbered: bool = True,
    model: str | None = None,
    user_id: UUID | None = None,
    verify: bool | None = None,
) -> list[dict[str, Any]]:
    """프로젝트 전체 절을 근거와 대조해 경고 행 목록을 만든다(저장은 안 함).

    겹침 판정이 건져 올린 의심 문장은 근거 원문과 함께 LLM에 한 번 더 묻는다
    (절당 1콜, 의심 문장이 있는 절만). 판정이 실패하면 겹침 결과를 그대로 쓴다.
    """
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(Section)
                    .where(Section.project_id == project_id, Section.content != "")
                    .order_by(Section.chapter_number, Section.section_number)
                )
            )
            .scalars()
            .all()
        )
        wanted: set[UUID] = set()
        for row in rows:
            wanted.update(u for u in (row.source_ids or []) if u)
        chunk_texts: dict[UUID, str] = {}
        if wanted:
            chunk_texts = {
                cid: text
                for cid, text in (
                    await session.execute(
                        select(Chunk.id, Chunk.content).where(Chunk.id.in_(wanted))
                    )
                ).all()
            }

    use_llm = settings.claim_verify_enabled if verify is None else verify
    out: list[dict[str, Any]] = []
    n_verified = 0
    for row in rows:
        claims, comparable = claims_for_section(row, chunk_texts, renumbered=renumbered)
        supported: set[int] = set()
        refuted: set[int] = set()
        candidates = suspicious_indices(claims) if (use_llm and comparable) else []
        if candidates:
            verdicts = await verify_claims(
                [claims[i] for i in candidates],
                chunk_texts,
                model=model,
                user_id=user_id,
                project_id=project_id,
                section_ref=f"{row.chapter_number}.{row.section_number}",
            )
            n_verified += len(verdicts)
            for pos, verdict in verdicts.items():
                # 판정은 후보 순번으로 오므로 원래 문장 위치로 되돌린다.
                target = candidates[pos]
                if verdict.is_supported:
                    supported.add(target)
                elif verdict.verdict == "not_supported":
                    refuted.add(target)
        out.extend(
            findings_from_claims(
                row, claims, comparable=comparable, supported=supported, refuted=refuted
            )
        )
    logger.info(
        "evidence_findings.done", project_id=str(project_id), n=len(out), verified=n_verified
    )
    return out
