"""파이프라인 단계 함수 — research / write / assemble.

- research: 목차 설계(플래너) → 웹 수집 → 청킹·임베딩 인덱싱. SOURCE_POOL 게이트 직전까지.
- write:    섹션별 검색→후보 생성→정적 게이트→자동 채택. run_write_loop 위임 후 곧장 조립.
- assemble: 사람이 고른 후보를 조립하고 보고서 레벨 정적검사(structure_complete) 후 HWPX 렌더.

의존성은 모듈 전역 주입식(_plan_client·_research_service_factory·_web_indexer_factory·
_retriever_factory·_write_client·_exporter) — 테스트는 이를 fake로 교체해 실검색/실LLM/
실DB 없이 파이프라인을 관통시킨다. 실제 LLM 호출은 token_context로 감싸져 토큰이 귀속된다.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import structlog

from src.clients.llm.base import LLMClient
from src.clients.llm.exceptions import LLMClientError
from src.clients.llm.token_tracker import token_context
from src.clients.parser.base import strip_replacement_chars
from src.core import app_settings
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import SectionPlan, SourceRef, SourceType
from src.services.generation.planner import plan_from_outline, plan_sections
from src.services.indexing.raptor import RaptorBuilder, build_raptor_builder
from src.services.indexing.web import WebSourceIndexer, build_web_source_indexer
from src.services.research import ResearchResult, ResearchSpec, WebResearchService
from src.services.retrieval.section import SectionRetriever
from src.workflows.events import emit_phase, emit_step
from src.workflows.write_loop import (
    auto_select_survivors,
    check_assembled,
    overlay_working_copy,
    run_write_loop,
)

logger = structlog.get_logger(__name__)

_PREVIEW_MAX_CHARS = 240


# web_fetch가 마크다운 앞에 붙이는 메타 머리말(--- canonical/meta-description ---).
# 미리보기·저장 본문에 남기면 사람 눈과 임베딩 양쪽에 노이즈다.
_WEB_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n.*?\n\s*---\s*\n?", re.DOTALL)


def strip_web_frontmatter(md: str) -> str:
    """web_fetch 메타 머리말 제거 — 본문이 머리말뿐이면 빈 문자열이 된다."""
    return _WEB_FRONTMATTER_RE.sub("", md, count=1)


_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# 링크·URL·구두점만으로 이뤄진 줄(내비게이션 메뉴·로고 등) — 본문 문장이 없다.
# 정규식으로 링크 전체를 한 번에 매칭하려 하면 링크 텍스트 안의 대괄호에 걸려 실패한다
# (실측: "[https://twitter.com/intent/tweet?text=...%20[New...]](...)" 형태의 공유 링크가
# 통과해 1,074자짜리 URL 덩어리가 그대로 임베딩됐고, 보고서가 그걸 6번 인용했다).
# 링크와 URL을 먼저 걷어낸 뒤 글자가 남는지 보는 편이 튼튼하다. 링크 텍스트는 최소
# 매칭으로 잡는다 - "](" + URL 이 나올 때까지 밀고 가므로 텍스트 안에 대괄호가 있어도
# 링크 전체를 삼킨다.
_MD_LINK_RE = re.compile(r"\[[^\n]*?\]\([^\s)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_MD_SYNTAX_RE = re.compile(r"[\s>*#|:•·—–×✕✖\-\[\]()!]+")
_WORD_RE = re.compile(r"[A-Za-z0-9가-힣]")


def _is_link_only(line: str) -> bool:
    """링크·URL·마크다운 기호를 걷어낸 뒤 글자가 하나도 안 남으면 본문이 아니다.

    링크 라벨까지 함께 지우는 게 핵심이다 - "[홈](/) [소개](/about)" 같은 메뉴 줄은
    라벨만 남기면 본문처럼 보인다. 반대로 "자세한 내용은 [보고서](url)에서"는 링크를
    걷어내도 문장이 남아 살아남는다.
    """
    rest = _URL_RE.sub(" ", _MD_LINK_RE.sub(" ", line))
    return not _WORD_RE.search(_MD_SYNTAX_RE.sub("", rest))


# 사이트 공통 배너 문구 — 이 마커가 든 줄은 본문이 아니다(정부 누리집 안내 배너,
# JS 렌더 사이트의 메뉴 자리표시자 'Loading…' 등)
_BOILERPLATE_MARKERS = (
    "googletagmanager.com",
    "javascript:void(0)",  # 클릭용 가짜 링크 — 어떤 사이트든 UI 요소
    "대한민국 공식 전자정부 누리집",
    "공식 누리집 주소 확인",
    "아이콘 또는 HTTPS 확인",
    "자물쇠 아이콘과 주소 앞",
    "- Loading…",
    # 한국 사이트 공통 접근성·검색 UI 문구(정부 누리집 헤더 등)
    "화면크기",
    "인기검색어",
    "최근검색어",
    "검색어 자동완성",
    "검색어를 입력",
    "본문 바로가기",
    "주메뉴 바로가기",
)
# 정리 후 이 분량(자)이 안 되면 실본문이 아니라 배너·잔재로 판정 — 검색 근거 불가
_MIN_CONTENT_CHARS = 200


def clean_web_markdown(md: str) -> str:
    """web_fetch 마크다운의 보일러플레이트 제거 — 미리보기·임베딩 오염 방지.

    Anthropic 페처는 페이지 전체를 마크다운으로 주므로 상단 내비게이션·로고 링크·
    추적 스크립트(GTM iframe) 잔재가 본문 앞에 붙는다(2026-08-03 실측). 머리말을
    벗기고, 이미지와 링크만으로 이뤄진 줄을 걷어내 문장이 있는 줄만 남긴다.
    """
    md = strip_replacement_chars(strip_web_frontmatter(md))
    kept: list[str] = []
    for line in md.splitlines():
        if any(marker in line for marker in _BOILERPLATE_MARKERS):
            continue
        no_img = _MD_IMAGE_RE.sub("", line)
        # 빈 줄은 문단 경계로 남긴다 - 청킹이 문단을 보고 자를 수 있어야 본문과
        # 페이지 가구가 한 청크에 안 섞인다.
        if not no_img.strip() or _is_link_only(no_img):
            if kept and kept[-1] != "":
                kept.append("")
            continue
        kept.append(no_img)
    return "\n".join(kept).strip()


# 문단 단위로 껍데기를 지워 임베딩 전에 빼는 안은 **실측 후 폐기했다**(2026-08-12).
# 인용됐던 청크 110개로 검사했더니 12개가 사라졌고, 그중 5개가 진짜 기사 본문이었다 -
# 해외 규제 동향("덴마크·뉴질랜드 규제 논의", "호주 청소년 SNS 470만 계정 폐쇄")과
# 국내 플랫폼 생태계 기사가 "알림/로그인/글자크기 설정" 같은 페이지 가구와 같은 문단
# 덩어리 안에 있었다. 되돌릴 수 없는 제거에서 11% 손실은 받을 수 없다.
#
# 여기 남긴 줄 단위 제거(링크·URL·이미지만 있는 줄)는 판정이 명확하고 손실이 관측된
# 적이 없다. 애매한 것은 색인 뒤 metadata.excluded 표시로 돌린다 - 원본 행이 남아
# 되돌릴 수 있고, 오탐이 나도 원문 대조 화면이 모델이 받은 것을 그대로 보여준다.


def has_usable_content(content_md: str | None) -> bool:
    """본문이 '검색 근거로 쓸 수 있는' 분량인지 판정.

    정부 누리집 배너처럼 회수는 됐지만 실내용이 첨부파일(PDF·HWP)에 있는 페이지는
    정리 후 몇 줄만 남는다(2026-08-03 고용노동부 매뉴얼 실측) — 이런 껍데기를
    '본문 있음'으로 치면 커버리지가 위장되고 사람도 속는다.
    """
    if not content_md:
        return False
    return len(clean_web_markdown(content_md)) >= _MIN_CONTENT_CHARS


# 발췌 키워드에서 뺄 조사·접속류 — 절 제목의 실질 명사만 매칭에 쓴다
_TITLE_STOPWORDS = {"및", "등", "관련", "위한", "대한", "중심", "기반", "그리고", "통한"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]{2,}")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _title_keywords(titles: list[str]) -> list[str]:
    kws: list[str] = []
    for t in titles:
        for tok in _TOKEN_RE.findall(t or ""):
            if tok not in _TITLE_STOPWORDS and tok not in kws:
                kws.append(tok)
    return kws


def relevance_excerpt(content_md: str | None, section_titles: list[str] | None) -> str | None:
    """절 제목 키워드가 가장 많이 등장하는 문장 주변 발췌 — 관련성의 눈에 보이는 근거.

    검색 스니펫을 우리 쪽에서 재현하는 장치다: 모델이 본 스니펫은 암호화라 저장이
    안 되므로, 회수한 본문에서 '이 절과 관련인 이유'를 결정적으로 뽑아 보여준다.
    매칭 문장이 없으면 None — 호출부가 본문 앞부분 미리보기로 폴백한다.
    """
    if not content_md or not section_titles:
        return None
    text = " ".join(clean_web_markdown(content_md).split())
    if not text:
        return None
    keywords = _title_keywords(list(section_titles))
    if not keywords:
        return None
    sentences = [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    best_i, best_hits = -1, 0
    for i, sent in enumerate(sentences):
        hits = sum(1 for k in keywords if k in sent)
        if hits > best_hits:
            best_i, best_hits = i, hits
    if best_i < 0:
        return None
    excerpt = sentences[best_i]
    j = best_i + 1
    while len(excerpt) < _PREVIEW_MAX_CHARS and j < len(sentences):
        excerpt += " " + sentences[j]
        j += 1
    if len(excerpt) > _PREVIEW_MAX_CHARS:
        excerpt = excerpt[:_PREVIEW_MAX_CHARS].rstrip() + "…"
    return ("…" if best_i > 0 else "") + excerpt


def _source_preview(content_md: str | None) -> str | None:
    """게이트 표시용 본문 미리보기 — 보일러플레이트 제거 후 첫 '문장다운' 줄부터.

    필터를 통과한 짧은 메뉴 잔재("Search Result ×" 류)가 앞머리에 남을 수 있어,
    30자 미만의 짧은 선두 줄들은 건너뛰고 제목/문장부터 미리보기를 시작한다
    (제목 줄 '# …'은 15자 이상이면 인정). 전부 짧으면 앞부분 그대로 폴백.
    """
    if not content_md:
        return None
    lines = [ln.strip() for ln in clean_web_markdown(content_md).splitlines() if ln.strip()]
    if not lines:
        return None
    start = 0
    for i, ln in enumerate(lines):
        if len(ln) >= 30 or (ln.startswith("#") and len(ln) >= 15):
            start = i
            break
    collapsed = " ".join(" ".join(lines[start:]).split())
    if not collapsed:
        return None
    if len(collapsed) <= _PREVIEW_MAX_CHARS:
        return collapsed
    return collapsed[:_PREVIEW_MAX_CHARS].rstrip() + "…"


_ECONOMY_MODEL = "claude-haiku-4-5"
# 절약 모드의 본문 작성 모델 — 4파전 실측(2026-08-08): 절당 $0.039로 Sonnet의 1/5인데
# 인용 분산·개조식 준수는 공동 1위. 대신 서술이 얕아 납품용은 standard를 쓴다.
_ECONOMY_WRITE_MODEL = "gpt-5.4-mini-2026-03-17"
# 파트 소주제 계획 전용 — 문서 구조(분석 틀 축 준수·소제목 위계)를 좌우하는데 절당
# 1콜·600토큰이라 비용이 무시할 수준이라, 절약 모드에서도 상위 모델을 쓴다.
# (Sonnet 5는 같은 역할에서 계획 실패 1/2회 관측 — 안정성 때문에 4.6 유지)
_PLAN_MODEL = "claude-sonnet-4-6"
# 고급 모드 — 수집은 Sonnet, 본문만 Opus. 수집은 도구를 여러 번 도는 루프라 상위
# 모델을 써도 회수량이 크게 늘지 않는 반면, 본문은 모델 품질이 그대로 문장에 남는다
# (사용자 요청 2026-08-11: "검색은 sonnet, 글 작성만 opus").
_PREMIUM_RESEARCH_MODEL = "claude-sonnet-4-6"
_PREMIUM_WRITE_MODEL = "claude-opus-5"
# 고급 모드는 파트 계획도 Opus로 쓴다(사용자 결정 2026-08-11). 절당 1콜·수백 토큰이라
# 비용 영향은 미미한데, 파트 소주제 구성이 문서 구조를 그대로 좌우한다.
_PREMIUM_PLAN_MODEL = "claude-opus-5"


def _models_for(state: ProjectState) -> dict[str, str]:
    """프로젝트 품질 모드(config.model_mode) → 역할별 모델.

    premium: 수집·검증은 Sonnet 4.6, 본문·파트 계획은 Opus 5(품질 우선).
    economy: 수집·검증은 Haiku, 본문은 gpt-5.4-mini(비용 우선). standard: 전역 설정
    (DB 오버라이드→env, 기본 Sonnet 4.6). 두 모드 모두 파트 계획만 _PLAN_MODEL.
    RAPTOR(gemini)·임베딩은 모드와 무관.
    """
    mode = state.options.get("model_mode") if isinstance(state.options, dict) else None
    if mode == "premium":
        return {
            "planner": _PREMIUM_RESEARCH_MODEL,
            "research": _PREMIUM_RESEARCH_MODEL,
            "write": _PREMIUM_WRITE_MODEL,
            "write_plan": _PREMIUM_PLAN_MODEL,
            "verify": _PREMIUM_RESEARCH_MODEL,
        }
    if mode == "economy":
        return {
            "planner": _ECONOMY_MODEL,
            "research": _ECONOMY_MODEL,
            "write": _ECONOMY_WRITE_MODEL,
            "write_plan": _PLAN_MODEL,
            "verify": _ECONOMY_MODEL,
        }
    return {
        "planner": app_settings.get_str("planner_model"),
        "research": app_settings.get_str("research_model"),
        "write": app_settings.get_str("write_model"),
        "write_plan": _PLAN_MODEL,
        "verify": app_settings.get_str("verify_model"),
    }


def _section_brief(sec: SectionPlan) -> str:
    """검색 질의용 절 브리프 — 제목만으론 못 찾는 절(예산·경제성)에 방향·관점을 실어준다."""
    parts = [f"{sec.chapter_number}.{sec.section_number} {sec.title}"]
    if sec.direction:
        parts.append(sec.direction)
    if sec.key_points:
        parts.append("핵심: " + ", ".join(sec.key_points[:4]))
    if sec.analysts:
        parts.append("관점: " + ", ".join(sec.analysts))
    return " — ".join(parts)


def _chapter_groups(state: ProjectState) -> list[tuple[int, str, list[str]]]:
    """챕터별 (번호, 제목, 절 제목들) — 분할 수집의 질의 단위.

    챕터 제목은 SectionPlan에 없어 config.outline에서 위치로 가져오고, 없으면
    'N장' 폴백(질의 topic 결합용이라 근사면 충분).
    """
    titles: dict[int, str] = {}
    outline = state.options.get("outline") if isinstance(state.options, dict) else None
    if isinstance(outline, dict):
        for i, ch in enumerate(outline.get("chapters") or [], start=1):
            if isinstance(ch, dict) and isinstance(ch.get("title"), str) and ch["title"].strip():
                titles[i] = ch["title"].strip()
    groups: dict[int, list[str]] = {}
    for s in state.section_plan:
        groups.setdefault(s.chapter_number, []).append(s.title)
    return [(n, titles.get(n, f"{n}장"), groups[n]) for n in sorted(groups)]


# 서버 도구 루프가 요청당 PDF 100페이지 상한을 넘겼을 때의 신호. 이 400은 응답이
# 아예 없어 클라이언트가 걷어낼 수 없다 — 회수 횟수를 줄여 다시 부르는 수밖에 없다.
_PDF_PAGE_LIMIT_SIGNAL = "maximum of 100 pdf pages"


def _is_pdf_page_limit_error(exc: Exception) -> bool:
    return _PDF_PAGE_LIMIT_SIGNAL in str(exc).lower()


async def _collect_chapter(
    spec: ResearchSpec,
    *,
    model: str,
    project_id: UUID,
    chapter: int,
) -> ResearchResult:
    """챕터 1개 수집 — PDF 100페이지 400이면 회수 횟수를 1로 줄여 1회 재시도.

    재시도가 없으면 큰 PDF 하나가 챕터를 통째로 0건으로 만든다(2026-08-06 실측:
    12콜 중 6콜 전멸 → 자료 6건). 재시도는 1회로 캡해 무한성을 유지한다.
    """
    service = _research_service_factory()
    try:
        return await service.collect(
            spec,
            model=model,
            max_uses=settings.research_max_uses,
            max_fetch_uses=settings.research_max_fetch_uses,
            max_tokens=settings.research_max_tokens,
        )
    except LLMClientError as exc:
        if not _is_pdf_page_limit_error(exc):
            raise
        logger.warning(
            "research.pdf_page_limit_retry",
            project_id=str(project_id),
            chapter=chapter,
            fetch_uses=settings.research_max_fetch_uses,
        )
        return await service.collect(
            spec,
            model=model,
            max_uses=settings.research_max_uses,
            max_fetch_uses=1,
            max_tokens=settings.research_max_tokens,
        )


def _search_scope(state: ProjectState) -> str:
    """검색 범위(국내/해외/모두) — 프로젝트 옵션에서 온다.

    국내 제도·통계가 본질인 보고서와 해외 기술 동향이 본질인 보고서는 정답이 달라
    한쪽으로 고정할 수 없다(2026-08-11). 값이 없거나 모르면 '모두'(개편 전 기본과
    같은 행동 — 신규 프로젝트는 폼이 항상 값을 쓴다).
    """
    from src.services.research.web_research import DEFAULT_SEARCH_SCOPE, SEARCH_SCOPES

    raw = state.options.get("search_scope") if isinstance(state.options, dict) else None
    return raw if raw in SEARCH_SCOPES else DEFAULT_SEARCH_SCOPE


async def _collect_sources(
    state: ProjectState,
    *,
    exclude_keys: set[str],
    target: int | None = None,
    ensure_coverage: bool = True,
    focus_titles: set[str] | None = None,
) -> list[SourceRef]:
    """챕터당 1콜 분할 수집 → URL 중복 제거 → 스테이징 → SourceRef 목록.

    보고서당 1콜은 full 보고서에 필요한 자료량(research_min_sources)이 구조적으로
    안 나온다(2026-08-03 실측 2~16건) — 챕터마다 해당 절 제목으로 질의를 좁혀
    수집 폭을 챕터 수 × research_max_uses로 늘린다(무한성 캡 유지).
    exclude_keys는 이미 풀에 있는 출처의 URL 키 — '추가 조사' 보충 라운드가
    기존 출처를 다시 담지 않게 한다.

    target은 이번 호출의 신규 출처 목표치(초기=research_min_sources,
    추가 조사=research_more_batch). ensure_coverage=True(초기 수집)면 1차 패스는
    전 챕터를 돌아 커버리지를 보장하고, 목표 미달 시 보충 패스 1회로 채운다.
    False(추가 조사)면 목표를 채우는 즉시 멈추고, 시작 챕터를 회전시켜 라운드마다
    앞 챕터 자료만 늘어나는 편향을 피한다. 비용 상한 = 챕터 수 × 2콜.

    focus_titles가 있으면 그 절만 질의 대상으로 남긴다(보충 수집을 '자료 0건 절'에
    겨냥). 이미 자료가 넉넉한 절을 다시 검색하면 dedup으로 버려질 결과만 나오고
    빈 절은 계속 빈 채로 남는다 — 실측에서 12,000자 강제 + 재료 없음이 수치 창작의
    직접 원인이었다(2026-08-09). 겨냥할 절이 없으면 전 챕터로 되돌린다.
    """
    pid = state.project_id
    indexer = _web_indexer_factory()
    refs: list[SourceRef] = []
    seen: set[str] = set(exclude_keys)
    chapters = _chapter_groups(state)
    if focus_titles:
        focused = [(n, t, [x for x in titles if x in focus_titles]) for n, t, titles in chapters]
        focused = [g for g in focused if g[2]]
        if focused:
            chapters = focused
    if not ensure_coverage and chapters:
        offset = len(exclude_keys) % len(chapters)
        chapters = chapters[offset:] + chapters[:offset]
    n_ok = 0
    last_error: Exception | None = None

    def _target_met() -> bool:
        # 목표는 '본문 있는(쓸 수 있는)' 자료 기준 — URL만 있는 껍데기는 세지 않는다
        if target is None:
            return False
        return sum(1 for r in refs if r.has_content) >= target

    for pass_no in (1, 2):
        if pass_no > 1 and (target is None or _target_met()):
            break
        # 보충 성격의 콜(2차 패스 또는 추가 조사 라운드)은 질의를 심화 쪽으로 틀어
        # 같은 검색 결과가 반복 회수(→전부 dedup)되는 낭비를 줄인다.
        supplement = pass_no > 1 or not ensure_coverage
        for ch_num, ch_title, section_titles in chapters:
            if supplement and _target_met():
                break
            label = f"자료 수집 · {ch_num}장 {ch_title}" + (" (보충)" if pass_no > 1 else "")
            emit_step(pid, "research", label, "started")
            topic = f"{state.topic} — {ch_title}"
            if supplement:
                topic += " (추가 심화 자료: 통계·사례·상반된 관점)"
            spec = ResearchSpec(
                topic=topic,
                report_type=state.preset or "blank",
                scope=_search_scope(state),
                outline=section_titles,
                briefs=[
                    _section_brief(sec)
                    for sec in state.section_plan
                    if sec.title in set(section_titles)
                ],
            )
            try:
                with token_context(
                    user_id=state.user_id,
                    project_id=state.project_id,
                    operation=f"research.collect:{ch_num}",
                ):
                    # 벽시계 상한 — 수집은 서버측 검색·회수를 도는 에이전틱 루프라
                    # 응답이 영영 안 오는 상태가 실제로 났다(2026-08-10: 28분 무활동,
                    # 화면은 '수집 중'). 끊고 다음 챕터로 넘어간다.
                    result = await asyncio.wait_for(
                        _collect_chapter(
                            spec,
                            model=_models_for(state)["research"],
                            project_id=state.project_id,
                            chapter=ch_num,
                        ),
                        timeout=settings.research_chapter_timeout_seconds,
                    )
            except TimeoutError as exc:
                emit_step(pid, "research", f"{label} (응답 없음)", "failed")
                # 전 챕터가 타임아웃이면 시스템 문제다 — 아래 n_ok==0 분기가
                # 실행 실패로 승격하도록 원인을 남긴다.
                last_error = LLMClientError(
                    f"{ch_num}장 수집이 {settings.research_chapter_timeout_seconds}초 안에 "
                    "끝나지 않았습니다"
                )
                logger.warning(
                    "research.chapter_timeout",
                    project_id=str(state.project_id),
                    chapter=ch_num,
                    timeout_s=settings.research_chapter_timeout_seconds,
                    error=str(exc),
                )
                continue
            except LLMClientError as exc:
                # 챕터 하나의 실패(재시도 소진·prompt too long 등)가 실행 전체를
                # 죽이지 않게 격리한다 — 해당 챕터만 건너뛰고 나머지를 계속 모아
                # 게이트를 열면, 사람이 '추가 조사'로 빈 챕터를 메울 수 있다.
                emit_step(pid, "research", label, "failed")
                last_error = exc
                logger.warning(
                    "research.chapter_failed",
                    project_id=str(state.project_id),
                    chapter=ch_num,
                    error=str(exc),
                )
                continue
            n_ok += 1
            emit_step(pid, "research", label, "completed")
            if result.coverage_gaps:
                logger.warning(
                    "research.coverage_gaps",
                    project_id=str(state.project_id),
                    chapter=ch_num,
                    gaps=result.coverage_gaps,
                )
            for src in result.sources:
                key = source_dedup_key(src.url, src.title)
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                content_md = clean_web_markdown(src.content_md or "")
                has_content = has_usable_content(content_md)
                if not has_content:
                    # 본문 없는 출처는 풀에 넣지 않는다 — 검색 근거가 못 되는 껍데기가
                    # 수십 건씩 쌓여 검토를 마비시켰다(2026-08-03 실측 127행 중 ~115
                    # 껍데기). 사이트별 보일러플레이트 필터 추격전도 여기서 끝낸다.
                    continue
                # 출처 저장(원문은 metadata_에). id는 게이트 payload의 SourceRef.id와 일치해야
                # 사람이 제외한 id가 그대로 색인 제외로 이어진다.
                source_id = await indexer.stage(
                    project_id=state.project_id,
                    content_md=content_md,
                    url=src.url,
                    title=src.title,
                    reliability=src.reliability,
                    matched_sections=list(src.matched_sections),
                    page_age=src.page_age,
                )
                # 신호를 SourceRef에 실어 게이트 payload로 — 사람이 취사선택할 근거.
                refs.append(
                    SourceRef(
                        id=source_id,
                        source_type=SourceType.WEB_SEARCH,
                        title=src.title or src.url,
                        url=src.url,
                        reliability=src.reliability,
                        matched_sections=list(src.matched_sections),
                        page_age=src.page_age,
                        preview=relevance_excerpt(content_md, list(src.matched_sections))
                        or _source_preview(content_md),
                        has_content=has_content,
                    )
                )
    if n_ok == 0 and last_error is not None:
        # 성공한 콜이 하나도 없으면 시스템 문제(키·네트워크·전면 한도)일 가능성이
        # 높다 — 빈 게이트를 여는 대신 실행 실패로 승격해 원인이 드러나게 한다.
        raise last_error
    return refs


def source_dedup_key(url: str | None, title: str | None) -> str:
    """출처 중복 판정 키 — URL 우선, 없으면 제목(정규화)."""
    return (url or title or "").strip().lower()


async def collect(state: ProjectState) -> ProjectState:
    """목차 확인 → 챕터 단위 웹 수집 → 출처 스테이징(원문 저장). SOURCE_POOL 게이트 직전까지.

    목차가 수집 질의(ResearchSpec.outline)의 입력이라 먼저 확정돼야 한다(사람 목차
    필수 정책 — outline이 없으면 레거시 LLM 플래너 폴백). 수집한 출처는
    project_sources에 저장하되 **임베딩은 하지 않는다** — 원문은 metadata_에 담겨
    확정 게이트 너머까지 살아남고, 사람이 채택한 출처만 이후 index 단계에서
    임베딩된다. 본문 없는 출처도 풀에는 남긴다 — 사람이 전체 풀을 보고 판단.
    """
    pid = state.project_id
    emit_phase(pid, "research", "started")
    if not state.section_plan:
        emit_step(pid, "research", "목차 설계", "started")
        outline = state.options.get("outline") if isinstance(state.options, dict) else None
        if outline:
            # 사용자가 생성 화면에서 확정한 목차 — LLM 생략, 본 그대로 실행된다.
            plan = plan_from_outline(outline)
        else:
            plan = await plan_sections(
                state.topic,
                state.preset or "blank",
                model=_models_for(state)["planner"],
                client=_plan_client,
                user_id=state.user_id,
                project_id=state.project_id,
            )
        state = state.with_section_plan(plan)
        emit_step(pid, "research", "목차 설계", "completed")

    # 부분 실패 재시작이면 state.sources에 이전 스테이징 출처가 복원돼 있다(runner).
    # 본문 있는 것만 제외해 중복을 막되, 본문 없는 껍데기는 재회수 기회를 준다
    # (성공 시 stage 업서트로 같은 행이 승격). 부족분 계산도 본문 기준.
    exclude = {
        key for s in state.sources if s.has_content and (key := source_dedup_key(s.url, s.title))
    }
    usable_existing = sum(1 for s in state.sources if s.has_content)
    remaining = max(0, settings.research_min_sources - usable_existing)
    refs = await _collect_sources(state, exclude_keys=exclude, target=remaining or None)
    logger.info(
        "research.collected",
        project_id=str(state.project_id),
        n_sources=len(refs),
        n_existing=len(state.sources),
    )
    emit_phase(pid, "research", "completed")
    # 재회수 승격분은 기존 껍데기 항목과 id가 같다 — 옛 버전을 걷어내고 병합.
    new_ids = {r.id for r in refs}
    if state.sources and new_ids:
        state = state.model_copy(
            update={"sources": [s for s in state.sources if s.id not in new_ids]}
        )
    return state.add_sources(refs)


async def index(state: ProjectState) -> ProjectState:
    """확정 이후 — 채택된(is_included) 웹 출처만 청킹·임베딩·색인 + RAPTOR.

    확정 게이트에서 사람이 제외한 출처는 is_included=false로 표시돼 load_included에서
    빠지므로 임베딩 자체를 건너뛴다(비용 절감). RAPTOR도 채택분만 요약해 제외 자료가
    요약 트리로 새지 않는다.
    """
    pid = state.project_id
    emit_phase(pid, "indexing", "started")
    emit_step(pid, "indexing", "청킹·임베딩·색인", "started")
    indexer = _web_indexer_factory()
    staged = await indexer.load_included(state.project_id)
    # 색인·RAPTOR는 수 분짜리 단계다 — 자료별/클러스터별 진행을 세부 단계로 발행해
    # 스테퍼가 "멈춘 것처럼" 보이지 않게 한다(2026-08-07 실사용 보고).
    usable = [s for s in staged if has_usable_content(s.content_md)]
    indexed: list[UUID] = []
    for i, src in enumerate(usable, start=1):
        title = f" · {src.title[:24]}" if src.title else ""
        emit_step(pid, "indexing", f"청킹·임베딩 {i}/{len(usable)}{title}", "started")
        result = await indexer.index_existing(
            project_id=state.project_id,
            source_id=src.source_id,
            # 색인 직전에도 보일러플레이트 정리 — 필터 도입 전에 저장된 행(구데이터)도
            # 임베딩 시점엔 깨끗한 본문을 쓰게 한다.
            content_md=clean_web_markdown(src.content_md),
        )
        if result.chunks_created:
            indexed.append(src.source_id)
    # 0청크로 끝난 출처(빈 본문·회수 실패 잔재)는 인용될 수 없다 — 채택으로 남기면
    # 전역 인용 번호 자리를 차지해 출처장에 유령 항목이 실린다. 재수집이 본문을
    # 채우면 stage()가 자동 제외를 되돌린다.
    from src.services.indexing.exclusion import auto_exclude_chunkless

    await auto_exclude_chunkless(
        state.project_id, [s.source_id for s in staged if s.source_id not in set(indexed)]
    )
    logger.info(
        "indexing.done",
        project_id=str(state.project_id),
        n_staged=len(staged),
        n_indexed=len(indexed),
    )
    emit_step(pid, "indexing", "청킹·임베딩·색인", "completed")
    if settings.raptor_enabled and indexed:
        emit_step(pid, "indexing", "배경 요약 트리(RAPTOR)", "started")
        try:
            n_nodes = await _raptor_builder_factory().build(
                state.project_id,
                depth_mode=state.depth_mode,
                user_id=state.user_id,
                on_progress=lambda msg: emit_step(pid, "indexing", msg, "started"),
            )
            logger.info("raptor.done", project_id=str(state.project_id), n_nodes=n_nodes)
            emit_step(pid, "indexing", "배경 요약 트리(RAPTOR)", "completed")
        except Exception:
            # RAPTOR는 품질 부스터지 필수 경로가 아니다 — 실패해도 파이프라인은 계속 간다.
            logger.warning("raptor.build_failed", project_id=str(state.project_id), exc_info=True)
            emit_step(pid, "indexing", "배경 요약 트리(RAPTOR)", "failed")
    emit_phase(pid, "indexing", "completed")
    return state.mark_indexed(indexed)


def _ensure_section_plan(state: ProjectState) -> ProjectState:
    """섹션 계획이 없으면 최소 계획으로 폴백.

    정상 경로는 research의 플래너가 계획을 만든다. 이 폴백은 계획 없이 write에
    진입한 비정상 흐름(레거시 데이터·테스트)에서 루프를 관통시키는 안전망이다.
    """
    if state.section_plan:
        return state
    logger.warning("write.plan_fallback", project_id=str(state.project_id))
    plan = [
        SectionPlan(chapter_number=1, section_number=1, title="개요"),
        SectionPlan(chapter_number=2, section_number=1, title="분석"),
    ]
    return state.with_section_plan(plan)


def _hyde_enabled_for(state: ProjectState) -> bool:
    """HyDE on/off — 프로젝트 config가 전역 기본값을 오버라이드한다.

    config.hyde_enabled가 있으면 그 값을, 없으면 settings.hyde_enabled(전역 기본).
    model_mode와 같은 프로젝트별 품질 노브 패턴을 따른다.
    """
    opts = state.options if isinstance(state.options, dict) else {}
    return bool(opts.get("hyde_enabled", settings.hyde_enabled))


def _default_retriever_factory(state: ProjectState) -> SectionRetriever:
    """실검색 retriever — 프로젝트 인덱스 대상 hybrid 검색에 바인딩.

    lazy import로 모듈 로드를 가볍게 유지(임베딩 모델 로딩 회피). research/index가
    아직 미배선이면 인덱스가 비어 빈 결과가 나올 수 있다(구조는 정상).
    """
    from src.clients.embedding_factory import get_embedding_client
    from src.db.session import async_session_maker
    from src.services.retrieval import HybridSearchClient
    from src.services.retrieval._keyword import KeywordSearchClient
    from src.services.retrieval._semantic import SemanticSearchClient
    from src.services.retrieval.section import make_section_retriever

    embedder = get_embedding_client()
    expander = None
    if _hyde_enabled_for(state):
        from src.services.retrieval._hyde import make_hyde_expander

        expander = make_hyde_expander(
            model=settings.hyde_model, user_id=state.user_id, project_id=state.project_id
        )
    semantic = SemanticSearchClient(async_session_maker, embedder, query_expander=expander)
    keyword = KeywordSearchClient(async_session_maker)
    hybrid = HybridSearchClient(semantic, keyword)

    reranker = None
    if settings.reranker_enabled:
        from src.clients.reranker_factory import get_reranker_client

        reranker = get_reranker_client()

    # 다국어 검색 — 근거 풀에 외국어 자료가 있을 때만 번역 콜을 건다. 국내 자료만 있는
    # 프로젝트가 대다수라 무조건 켜면 절마다 헛콜이 하나씩 붙는다. 판단은 첫 검색
    # 시점에 한 번(여기는 동기 팩토리라 DB를 못 본다).
    translator = None
    if settings.multilingual_search_enabled:
        from src.services.retrieval._multilingual import make_gated_translator

        translator = make_gated_translator(
            async_session_maker,
            state.project_id,
            model=settings.multilingual_query_model,
            min_foreign_ratio=settings.multilingual_min_foreign_ratio,
            user_id=state.user_id,
        )

    summary_fetcher = None
    if settings.raptor_enabled:
        from src.services.retrieval._raptor import make_summary_fetcher

        summary_fetcher = make_summary_fetcher(
            async_session_maker, embedder, state.project_id, top_k=settings.raptor_top_k
        )
    return make_section_retriever(
        hybrid,
        state.project_id,
        reranker=reranker,
        summary_fetcher=summary_fetcher,
        translate=translator,
        # 주제 앵커 — 재채점·RAPTOR 검색이 절 제목만으로 표류하지 않게(주제 표류 실측 대응)
        topic=state.topic,
        # 절당 근거 공급량 — 분량의 1차 병목 레버(config.retrieval_top_k)
        top_k=settings.retrieval_top_k,
    )


def _default_exporter(
    state: ProjectState, glossary: dict[str, dict[str, str]] | None = None
) -> Path:
    """조립된 보고서를 HWPX로 렌더. lazy import로 hwpx 의존을 사용 시점으로 미룬다."""
    from src.services.export.report import export_report

    return export_report(state, glossary=glossary)


async def _owner_name(user_id: UUID) -> str:
    """표지 작성자 표기용 소유자 이름 — 실행 중엔 필요 없어 렌더 직전에만 읽는다."""
    from src.db.models.user import User
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        owner = await session.get(User, user_id)
        return owner.name if owner else ""


async def _adopted_source_refs(project_id: UUID) -> list[SourceRef]:
    """채택(is_included) 자료를 출처 최종장용 SourceRef로 로드.

    조립·재개 시점의 state는 프로젝트 행에서 복원돼 sources가 비어 있다 —
    렌더 직전에 DB에서 채택분을 실어줘야 출처장이 생긴다(2026-08-05 실측 수정).
    """
    from sqlalchemy import select

    from src.db.models.project_source import ProjectSource
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(ProjectSource)
                    .where(
                        ProjectSource.project_id == project_id,
                        ProjectSource.is_included.is_(True),
                    )
                    .order_by(ProjectSource.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [
        SourceRef(
            id=r.id,
            source_type=SourceType(r.source_type),
            title=r.title or r.url or "(제목 없음)",
            url=r.url,
            reliability=r.reliability,
        )
        for r in rows
    ]


async def _default_section_store(state: ProjectState) -> None:
    """선택 확정된 섹션을 sections 테이블에 영구 저장. lazy import로 DB 의존을 미룬다."""
    from src.services.sections import persist_sections

    await persist_sections(state)


async def _default_draft_store(state: ProjectState, plan, draft) -> None:
    """작성 중 절 초안을 증분 저장(미리보기용). lazy import로 DB 의존을 미룬다."""
    from src.services.sections import persist_draft_section

    await persist_draft_section(state, plan, draft)


async def _default_rule_texts(owner_id, selected: list) -> list[str]:
    """이 보고서에 적용할 작성 규칙 텍스트 — 프로젝트에서 고른 개인 규칙을 반영.

    선택이 없으면 회사 표준 3종 그대로다. 규칙은 절이 아니라 보고서 단위 계약이라
    프로젝트 config.rules에서 한 번 정해 전 절에 같은 규칙이 적용된다.
    """
    from src.db.session import async_session_maker
    from src.services.prompts import resolve_rules

    async with async_session_maker() as session:
        return await resolve_rules(session, owner_id, selected)


async def _default_analyst_catalog(owner_id) -> dict:
    """개인→시스템 병합 에이전트 카탈로그(id·name 양쪽 키) — 작성기 주입용.

    작성기는 순수 모듈이라 DB를 못 읽는다. 실행 시작 시 한 번 만들어 넘겨야 사용자가
    만든 개인 에이전트가 실제 작성에 반영된다(그전엔 조용히 무시됐다).
    """
    from src.db.session import async_session_maker
    from src.services.prompts import resolve_analysts

    async with async_session_maker() as session:
        specs = await resolve_analysts(session, owner_id)
    catalog: dict = {}
    for spec in specs:
        catalog[spec.id] = spec
        catalog[spec.name] = spec
    return catalog


async def _default_working_copy(project_id) -> dict:
    """sections 행(사람이 고친 작업 사본)을 절 id → (본문, 인용) 으로 읽어온다."""
    import uuid as _uuid

    from sqlalchemy import select

    from src.db.models.section import Section
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            (await session.execute(select(Section).where(Section.project_id == project_id)))
            .scalars()
            .all()
        )
    return {r.id: (r.content, [_uuid.UUID(str(x)) for x in r.source_ids]) for r in rows}


async def _default_sections_cleaner(project_id) -> None:
    """write 시작 시 이전 런 sections 잔재 제거. lazy import로 DB 의존을 미룬다."""
    from src.services.sections import clear_project_sections

    await clear_project_sections(project_id)


async def _default_pm_verifier(state: ProjectState) -> int:
    """PM 검증 리포트 생성·저장(챕터당 1콜). lazy import로 LLM·DB 의존을 미룬다."""
    from src.services.qa.pm_verify import run_pm_verify

    return await run_pm_verify(state, model=_models_for(state)["verify"])


# 주입 지점 — 테스트는 이 전역들을 fake로 교체한다.
_plan_client: LLMClient | None = None
_research_service_factory: Callable[[], WebResearchService] = WebResearchService
_web_indexer_factory: Callable[[], WebSourceIndexer] = build_web_source_indexer
_raptor_builder_factory: Callable[[], RaptorBuilder] = build_raptor_builder
_retriever_factory: Callable[[ProjectState], SectionRetriever] = _default_retriever_factory
_write_client: LLMClient | None = None
_exporter: Callable[[ProjectState, dict[str, dict[str, str]] | None], Path] = _default_exporter
_section_store: Callable[[ProjectState], Awaitable[None]] = _default_section_store
_draft_store = _default_draft_store
_sections_cleaner = _default_sections_cleaner
_working_copy = _default_working_copy
_analyst_catalog = _default_analyst_catalog
_rule_texts = _default_rule_texts
_pm_verifier: Callable[[ProjectState], Awaitable[int]] = _default_pm_verifier


def _selected_rule_ids(state: ProjectState) -> list[UUID]:
    """config.rules(개인 작성 규칙 id) 파싱 — 잘못된 값은 조용히 버린다(검증은 생성 시)."""
    raw = state.options.get("rules") if isinstance(state.options, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[UUID] = []
    for x in raw:
        try:
            out.append(UUID(str(x)))
        except (ValueError, TypeError):
            continue
    return out


async def write(state: ProjectState) -> ProjectState:
    """섹션별 후보 생성 + 정적 게이트 + 생존 후보 자동 채택.

    QA 게이트 제거(2026-08-07) — 사람 검토는 완성 후 통합 화면(편집·재작성)에서
    한다. 절이 완성될 때마다 초안을 sections에 증분 저장해(_draft_store) 그 화면이
    작성 중에도 완성분부터 보여준다 — 확정본 전량 교체는 여전히 assemble 몫.
    """
    state = _ensure_section_plan(state)
    retrieve = _retriever_factory(state)
    emit_phase(state.project_id, "writing", "started")
    await _sections_cleaner(state.project_id)  # 이전 런 잔재 제거(증분 초안과 혼재 방지)
    models = _models_for(state)
    catalog = await _analyst_catalog(state.user_id)
    rules = await _rule_texts(state.user_id, _selected_rule_ids(state))
    result = await run_write_loop(
        state,
        retrieve=retrieve,
        client=_write_client,
        model=models["write"],
        plan_model=models["write_plan"],
        draft_store=_draft_store,
        analyst_catalog=catalog,
        rules=rules,
    )
    emit_phase(state.project_id, "writing", "completed")
    return auto_select_survivors(result)


async def assemble(state: ProjectState) -> ProjectState:
    """자동 채택된 후보를 조립·정적검사 후 HWPX 파일로 렌더.

    structure_complete 실패(누락 섹션)면 렌더를 건너뛰고 로깅한다 — 미완성 보고서를
    산출물로 내보내지 않는다(향후 FINAL 게이트로 사람에게 되돌릴 지점).
    """
    pid = state.project_id
    emit_phase(pid, "export", "started")
    emit_step(pid, "export", "통합·교정·HWPX 변환", "started")
    # 작성 중/직후에 사람이 고친 sections 행이 인메모리 후보보다 우선 — 이게 없으면
    # 미리보기에서 편집·재작성한 내용이 조립 때 조용히 덮어써진다(2026-08-09 지적).
    # resume 경로(runner)와 같은 규칙을 straight-through 경로에도 적용한다.
    try:
        state = overlay_working_copy(state, await _working_copy(pid))
    except Exception:
        logger.warning("assemble.working_copy_failed", project_id=str(pid), exc_info=True)
    # 인용 전역 번호화 — 절-로컬 [n]을 출처장 번호로 재작성(저장·검증·렌더 전부
    # 전역 번호 기준이 되도록 가장 먼저). 실패는 비치명 — 로컬 번호로 계속한다.
    try:
        from src.services.sections.renumber import renumber_state

        state = await renumber_state(state)
    except Exception:
        logger.warning("assemble.renumber_failed", project_id=str(pid), exc_info=True)
    drafts, result = check_assembled(state)
    logger.info(
        "assemble.done",
        project_id=str(state.project_id),
        selected=len(drafts),
        structure_ok=result.passed,
        detail=result.detail,
    )
    # 선택 확정 섹션을 정규 테이블에 저장 — 사후 조회·편집(/sections)의 원천.
    # 렌더 성공 여부와 무관하게 저장한다(부분 완성도 열람 가능해야 함).
    await _section_store(state)
    if settings.pm_verify_enabled and drafts:
        emit_step(pid, "export", "PM 검증 리포트", "started")
        try:
            n_findings = await _pm_verifier(state)
            emit_step(pid, "export", "PM 검증 리포트", "completed")
            logger.info(
                "assemble.pm_verify", project_id=str(state.project_id), n_findings=n_findings
            )
        except Exception:
            # 경고 리포트는 품질 보조 — 실패해도 렌더·완료를 막지 않는다.
            logger.warning(
                "assemble.pm_verify_failed", project_id=str(state.project_id), exc_info=True
            )
            emit_step(pid, "export", "PM 검증 리포트", "failed")
    if result.passed and drafts:
        # 출처 최종장 — 재개 복원 state는 sources가 비어 있어 렌더 직전 DB에서 채운다.
        try:
            refs = await _adopted_source_refs(state.project_id)
            if refs and not state.sources:
                state = state.model_copy(update={"sources": refs})
        except Exception:
            logger.warning("assemble.sources_load_failed", project_id=str(pid), exc_info=True)
        # 약어 사전 — 설명 열을 LLM 1콜(최저가)로 채우고 영속화(다운로드 재렌더용).
        glossary: dict[str, dict[str, str]] | None = None
        try:
            from src.services.export.glossary import build_glossary, persist_glossary

            glossary = await build_glossary(state)
            await persist_glossary(state.project_id, glossary)
        except Exception:
            # 설명은 장식 — 실패해도 풀네임만으로 렌더를 계속한다.
            logger.warning("assemble.glossary_failed", project_id=str(pid), exc_info=True)
        # 요약문 — 장별 압축을 LLM 1콜(최저가)로 만들어 config에 영속화(재렌더 공용).
        try:
            from src.services.export.summary import build_summary, persist_summary

            summary = await build_summary(state)
            if summary:
                await persist_summary(state.project_id, summary)
                state = state.model_copy(
                    update={"options": {**(state.options or {}), "summary": summary}}
                )
        except Exception:
            # 요약문은 전문(前文) 보조 — 실패해도 렌더를 계속한다.
            logger.warning("assemble.summary_failed", project_id=str(pid), exc_info=True)
        # 표지 작성자 — 소유자 이름. 실패해도 작성자 줄만 빠진다(렌더는 계속).
        if not state.author:
            try:
                state = state.model_copy(update={"author": await _owner_name(state.user_id)})
            except Exception:
                logger.warning("assemble.author_load_failed", project_id=str(pid), exc_info=True)
        path = _exporter(state, glossary)
        logger.info("assemble.exported", project_id=str(state.project_id), path=str(path))
    else:
        logger.warning(
            "assemble.export_skipped",
            project_id=str(state.project_id),
            detail=result.detail or "선택된 초안 없음",
        )
    emit_step(pid, "export", "통합·교정·HWPX 변환", "completed")
    # export/completed 가 프론트의 '완료' 신호(별도 done 프레임 없음).
    emit_phase(pid, "export", "completed")
    return state
