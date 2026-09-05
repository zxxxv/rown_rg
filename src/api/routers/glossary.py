"""정본 용어집 API — 검토 화면 승격 사슬의 백엔드(2026-09-04).

검토 화면의 용어 경고(상충·요동·불일치)를 사람이 처리하는 순간이 승격이다:
표기를 확정(POST)하면 glossary_terms에 남고, 다음 작성부터 주입이 강제하고
검사가 잣대로 쓴다. 미확정 후보는 저장하지 않는다 — 조립 때마다 경고로
재계산되므로 잃어버릴 수 없고, candidates 조회가 그 파생을 그대로 돌려준다.

관용 후보(suggest)는 문맥 없는 flash-lite 번역이다. 실측(2026-09-04, 실구글
API·상위 모델 3자 비교)으로 확정: 기관명·법명 표준 표기는 flash-lite가 실구글
NMT보다 정확했고 상위 모델과 동급이라, 후보 생성은 최저가 모델이 정답이다.
어디까지나 **후보**다 — 문서 표기가 맞는 경우가 더 많았으므로(웹 관용 재판정
10종 중 문서 승 4·번역 승 2~3) 자동 채택은 없고 사람이 고른다.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import require_writer
from src.core.config import settings
from src.db.models.glossary_term import GlossaryTerm
from src.db.models.user import User
from src.services.generation.term_rules import (
    GLOSSARY_ORIGIN,
    _pair_conflict_key,
    _pair_conflicts,
    load_project_terms,
    normalize_term_key,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/glossary", tags=["glossary"])

_SOURCES = {"document", "convention", "manual"}
_MAX_SUGGEST_TERMS = 40


class GlossaryTermRead(BaseModel):
    id: UUID
    project_id: UUID | None
    term_key: str
    en: str | None
    abbr: str | None
    ko: str
    definition: str | None
    source: str
    note: str | None


class GlossaryTermWrite(BaseModel):
    """확정(승격) 입력 — en/abbr 중 하나는 있어야 키가 선다."""

    project_id: UUID | None = None  # None = 회사 공유(전 프로젝트)
    en: str | None = Field(default=None, max_length=160)
    abbr: str | None = Field(default=None, max_length=40)
    ko: str = Field(min_length=1, max_length=120)
    definition: str | None = None
    source: str = "manual"  # document | convention | manual
    note: str | None = None


class GlossaryCandidateVariant(BaseModel):
    """상충 용어의 표기 변형 하나 — 어느 자료가 어떤 문맥에서 그렇게 썼나."""

    ko: str
    source_title: str
    context: str | None


class GlossaryCandidateRead(BaseModel):
    """미확정 상충 후보 — 채굴 용어표에서 파생, 저장하지 않는다."""

    term_key: str
    en: str | None
    abbr: str | None
    variants: list[GlossaryCandidateVariant]


@router.get("", response_model=list[GlossaryTermRead])
async def list_glossary(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[User, Depends(get_current_active_user)],
    project_id: UUID | None = None,
) -> list[GlossaryTermRead]:
    """정본 목록 — 회사 공유 전체 + (project_id 주면) 그 프로젝트의 덮어쓰기."""
    cond: Any = GlossaryTerm.project_id.is_(None)
    if project_id is not None:
        cond = cond | (GlossaryTerm.project_id == project_id)
    rows = (
        (await session.execute(select(GlossaryTerm).where(cond).order_by(GlossaryTerm.term_key)))
        .scalars()
        .all()
    )
    return [GlossaryTermRead.model_validate(r, from_attributes=True) for r in rows]


@router.post("", response_model=GlossaryTermRead)
async def confirm_term(
    data: GlossaryTermWrite,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
) -> GlossaryTermRead:
    """표기 확정(승격) — 같은 층·같은 키가 있으면 덮어쓴다(재확정은 정정이다)."""
    en = (data.en or "").strip() or None
    abbr = (data.abbr or "").strip() or None
    if not (en or abbr):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "en 또는 abbr이 필요합니다")
    if data.source not in _SOURCES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"source는 {_SOURCES} 중 하나")
    key = normalize_term_key(en, abbr)
    scope_cond = (
        GlossaryTerm.project_id.is_(None)
        if data.project_id is None
        else GlossaryTerm.project_id == data.project_id
    )
    row = (
        await session.execute(select(GlossaryTerm).where(scope_cond, GlossaryTerm.term_key == key))
    ).scalar_one_or_none()
    if row is None:
        row = GlossaryTerm(project_id=data.project_id, term_key=key)
        session.add(row)
    row.en = en
    row.abbr = abbr
    row.ko = data.ko.strip()
    row.definition = (data.definition or "").strip() or None
    row.source = data.source
    row.note = (data.note or "").strip() or None
    row.created_by = current_user.id
    await session.flush()
    await session.refresh(row)
    logger.info(
        "glossary.confirmed",
        term_key=key,
        ko=row.ko,
        scope="project" if data.project_id else "org",
        source=data.source,
    )
    return GlossaryTermRead.model_validate(row, from_attributes=True)


@router.delete("/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_term(
    term_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[User, Depends(require_writer)],
) -> None:
    """확정 철회 — 지우면 그 키는 다시 채굴 용어표의 보수 모드(상충 경고)로 돌아간다."""
    row = await session.get(GlossaryTerm, term_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 항목입니다")
    await session.delete(row)


@router.get("/candidates", response_model=list[GlossaryCandidateRead])
async def list_candidates(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[GlossaryCandidateRead]:
    """미확정 상충 후보 — 이 프로젝트 용어표에서 표기가 갈렸는데 정본이 없는 키들.

    주입의 다수결(_pair_conflicts)과 같은 판정을 쓴다 — 화면이 보는 상충과 작성이
    보수화하는 상충이 어긋나면 안 된다. 정본이 이미 있는 키는 후보가 아니다.
    """
    # 순환 임포트 회피 — 권한 판정만 빌려 쓴다(verify_coverage와 같은 관례).
    from src.api.routers.projects import _get_authorized_project

    await _get_authorized_project(project_id, session, current_user)
    entries = await load_project_terms(project_id)
    confirmed = {_pair_conflict_key(e) for e in entries if e.get("origin") == GLOSSARY_ORIGIN}
    mined = [e for e in entries if e.get("origin") != GLOSSARY_ORIGIN]
    conflicts = _pair_conflicts(mined)
    out: list[GlossaryCandidateRead] = []
    for key in sorted(conflicts):
        if key in confirmed:
            continue
        variants: list[GlossaryCandidateVariant] = []
        seen: set[tuple[str, str]] = set()
        en: str | None = None
        abbr: str | None = None
        for e in mined:
            if _pair_conflict_key(e) != key:
                continue
            en = en or e.get("en")
            abbr = abbr or e.get("abbr")
            mark = (str(e.get("ko")), str(e.get("source_title") or ""))
            if mark in seen:
                continue
            seen.add(mark)
            variants.append(
                GlossaryCandidateVariant(
                    ko=str(e["ko"]),
                    source_title=str(e.get("source_title") or ""),
                    context=e.get("context") or e.get("definition"),
                )
            )
        out.append(GlossaryCandidateRead(term_key=key, en=en, abbr=abbr, variants=variants))
    return out


class SuggestRequest(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=_MAX_SUGGEST_TERMS)


@router.post("/suggest", response_model=dict[str, str])
async def suggest_conventions(
    data: SuggestRequest,
    current_user: Annotated[User, Depends(require_writer)],
) -> dict[str, str]:
    """관용 표기 후보 — 문맥 없는 flash-lite 번역 1콜. 자동 채택 없음, 후보 표시용."""
    from src.clients.llm.base import CompletionRequest, Message
    from src.clients.llm.factory import get_llm_client
    from src.clients.llm.token_tracker import token_context

    terms = [t.strip() for t in data.terms if t.strip()][:_MAX_SUGGEST_TERMS]
    if not terms:
        return {}
    prompt = (
        "다음 영어 용어들을 한국어로 번역하라. 문맥은 없다 - 각 용어의 가장 표준적이고"
        " 널리 쓰이는 한국어 표기 하나만 답하라. 설명·병기 없이 표기만.\n"
        '출력은 JSON 하나: {"용어영문": "한국어표기", ...}\n\n' + "\n".join(f"- {t}" for t in terms)
    )
    try:
        with token_context(user_id=current_user.id, operation="glossary.suggest"):
            resp = await get_llm_client().complete(
                CompletionRequest(
                    model=settings.term_mining_model,
                    messages=[Message(role="user", content=prompt)],
                    max_tokens=4000,
                    temperature=0.0,
                )
            )
    except Exception:
        logger.warning("glossary.suggest_failed", n_terms=len(terms), exc_info=True)
        return {}
    text = resp.content
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed: Any = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    wanted = {t.lower(): t for t in terms}
    return {
        wanted[str(k).lower()]: str(v).strip()
        for k, v in (parsed.items() if isinstance(parsed, dict) else [])
        if isinstance(v, str) and v.strip() and str(k).lower() in wanted
    }
