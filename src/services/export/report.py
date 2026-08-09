"""조립된 보고서 → HWPX 파일 — 파이프라인과 hwpx_writer 사이의 변환 계층.

선택된 섹션 초안(LLM이 쓴 마크다운)을 hwpx_writer의 Block 시퀀스로 바꿔 렌더한다.
실제 보고서 꼴을 갖추도록 **표지 → 목차 → 본문(장별)** 순으로 조립한다.

작성 규칙(src/prompts) 반영:
- 첫 장은 표지(제목·기관·날짜), 둘째 장은 목차다.
- 본문은 개조식(□ 대주제 → ㅇ → - → *)으로, 마커 수준마다 들여쓰기해 렌더한다.
- 약어는 "풀네임(약어)" 형식으로 첫 등장하므로(agent_writing_style.md), 장 단위로 수집해
  각 장 끝에 "약어 정리"표로 정리한다.
- 표가 없는 절에는 추천 시각자료(그림) 자리표시자를 넣는다(2페이지당 시각자료 1개 규칙).
- 표 셀의 인라인 마크다운(**강조**·링크)은 벗기고, 표 폭은 본문 폭에 맞춰 긴 셀이 줄바꿈된다.
- 인용 마커 [n]은 **전역 번호**(조립 시 renumber로 참고문헌장 번호에 재매핑됨)라 본문에
  그대로 남긴다 — 독자가 본문 [n] ↔ 참고문헌 최종장 [n]을 맞춰 검증할 수 있다(2026-08-05).
- 번호는 텍스트에 직접 넣는다("제1장", "1.1 …"). 헤딩에 개요 자동번호를 부여하지 않아
  한컴이 제목·목차 앞에 "1.", "2." 같은 개요 번호를 붙이지 않는다.
"""

from __future__ import annotations

import re
from datetime import UTC, timedelta, timezone
from pathlib import Path

import structlog

from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import SectionPlan, SourceRef, SourceType
from src.export.hwpx_writer import (
    HEADER_TEXT,
    Block,
    Cover,
    Figure,
    Heading,
    PageBreak,
    Paragraph,
    Table,
    build_report,
)

# 한국은 DST가 없어 UTC+9 고정 오프셋으로 표시 계층 변환이 정확하다(tzdata 의존 없음).
_KST = timezone(timedelta(hours=9))

logger = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\s*\[\d+\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")

# 본문 내 마크다운 헤딩이 렌더될 개요 수준 (섹션 제목=2 아래).
_CONTENT_HEADING_LEVEL = 3

# 개조식 마커 → 들여쓰기 수준. 값이 클수록 더 깊게 들여쓴다.
_OUTLINE_LEVEL_BY_MARKER: dict[str, int] = {"□": 0, "ㅇ": 1, "○": 1, "◦": 1, "-": 2, "*": 3}

# "풀네임(약어)" 첫 등장 표기 추출. 약어=대문자 시작 영문 2자 이상, 풀네임은 괄호 바로 앞의
# 영문 구(제목격 시작) 또는 한글 구(최대 4어절 — "월간 활성 사용자(MAU)"처럼 여러 어절
# 명칭이 마지막 한 단어만 잡히던 빈약함 수정, 2026-08-05). 괄호 앞 공백은 허용하지 않아
# "비율 (B/C)" 같은 비(非)약어 괄호 표기를 배제한다.
_ABBR_RE = re.compile(
    r"(?P<full>"
    r"[A-Z][A-Za-z0-9.&/\-]*(?:\s+[A-Za-z0-9.&/\-]+)*"  # 영문 구: "Small Modular Reactor"
    # 한글 구(1~4어절): 선행 어절은 2자 이상만 — '와/의' 같은 1자 조사가 딸려 오지 않게.
    # 구분자는 공백만(개행 제외)이라 줄을 넘어 캡처하지 않는다.
    r"|(?:[가-힣][가-힣A-Za-z0-9]+ ){0,3}[가-힣][가-힣A-Za-z0-9]*"
    r")\((?P<abbr>[A-Z][A-Za-z0-9&/\-]+)\)"
)
# 풀네임 병기가 생략되는 일반 상식 약어(agent_writing_style.md) — 정리표에서 제외.
_COMMON_ABBR: frozenset[str] = frozenset({"AI", "IT", "API", "GDP", "UN", "EU", "US", "UK", "OECD"})


def _strip_citations(text: str) -> str:
    """[n] 인용 마커 제거."""
    return _CITATION_RE.sub("", text)


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")

# 모델이 발명한 비표준 대괄호 마커('[배경자료 제공됨]' 등, 배경 요약 오염 신호) 제거.
# 정의는 QA 게이트(qa/gate.py check_citation_markers)와 동일 — 허용은 [n]·[그림 …]·[표 …]뿐.
# 마크다운 링크 [텍스트](url)는 (?!\() 로, 정상 [n] 인용은 허용 목록으로 보존한다.
_INVENTED_MARKER_RE = re.compile(r" ?\[([^\[\]\n]{1,40})\](?!\()")
_ALLOWED_BRACKET_RE = re.compile(r"^(?:\d+|그림\s?[-\d. ]+|표\s?[-\d. ]+)$")


def _strip_invented_markers(text: str) -> str:
    """[n]·[그림/표 …]가 아닌 대괄호 마커를 앞 공백까지 함께 제거한다."""
    return _INVENTED_MARKER_RE.sub(
        lambda m: m.group(0) if _ALLOWED_BRACKET_RE.match(m.group(1).strip()) else "",
        text,
    )


def _clean_inline(text: str) -> str:
    """HWPX 평문 렌더에서 의미 없는 인라인 기호 제거(강조·링크·발명 인용 마커)."""
    text = text.replace("**", "")
    text = _MD_LINK_RE.sub(r"\1", text)
    return _strip_invented_markers(text)


def _outline_level(line: str) -> int | None:
    """개조식 마커로 시작하면 들여쓰기 수준을, 아니면 None을 반환한다."""
    if len(line) >= 2 and line[1] == " ":
        return _OUTLINE_LEVEL_BY_MARKER.get(line[0])
    return None


def _collect_abbreviations(text: str, acc: dict[str, str]) -> None:
    """text의 "풀네임(약어)" 표기를 acc(약어→풀네임)에 첫 등장 순서로 누적한다."""
    for match in _ABBR_RE.finditer(text):
        abbr = match.group("abbr")
        if abbr in _COMMON_ABBR or abbr in acc:
            continue
        acc[abbr] = _clean_inline(match.group("full")).strip()


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(_TABLE_SEP_CELL_RE.match(c) for c in cells if c) and any(cells)


def markdown_to_blocks(md: str) -> list[Block]:
    """마크다운 본문을 Heading/Paragraph/Table 블록으로 변환.

    지원 범위는 LLM 산출물에서 실제로 나오는 형태(헤딩·개조식 문단·GFM 표)로 한정한
    보수적 파서다. 개조식 마커(□ ㅇ - *)는 마커를 보존한 채 수준별로 들여쓰기하며,
    각 마커 줄은 독립 문단이 된다. 인식 못 하는 줄은 문단으로 강등된다(내용 유실 없음).
    """
    blocks: list[Block] = []
    para_buf: list[str] = []
    table_buf: list[list[str]] = []

    def flush_para() -> None:
        if para_buf:
            blocks.append(Paragraph(text=_clean_inline(" ".join(para_buf))))
            para_buf.clear()

    def flush_table() -> None:
        if not table_buf:
            return
        rows = [cells for cells in table_buf if not _is_separator_row(cells)]
        table_buf.clear()
        if rows:
            # 셀 안의 인라인 마크다운(**강조**·[링크])을 벗겨 평문으로 렌더한다.
            cleaned = [[_clean_inline(c) for c in row] for row in rows]
            blocks.append(Table(headers=cleaned[0], rows=cleaned[1:]))

    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            flush_para()
            flush_table()
            continue
        if _HR_RE.match(line):
            # 마크다운 구분선(---)은 시각 장식 — 문서에 리터럴로 남기지 않는다.
            flush_para()
            flush_table()
            continue
        if line.startswith("|"):
            flush_para()
            table_buf.append(_table_cells(line))
            continue
        flush_table()
        if line.startswith(">"):
            # 인용 블록(주석·제약 사항) — '>' 기호를 벗기고 한 단계 들여쓴 문단으로.
            flush_para()
            quoted = _clean_inline(line.lstrip("> ").strip())
            if quoted:
                blocks.append(Paragraph(text=quoted, indent=1))
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_para()
            text = _clean_inline(heading.group(2)).strip()
            marker_level = _outline_level(text)
            if marker_level is not None:
                # "## □ 추진 배경"류 — 마커 달린 헤딩은 개조식 문단으로 강등해
                # 작성 규칙(□→ㅇ→-)의 계층을 살린다(전부 헤딩3으로 눌리면 위계 붕괴).
                blocks.append(Paragraph(text=text, indent=marker_level))
            elif len(heading.group(1)) <= 2:
                blocks.append(Heading(level=_CONTENT_HEADING_LEVEL, text=text))
            else:
                # ###+ 무마커 소제목은 최상위 개조식(□ 상당) 문단으로.
                blocks.append(Paragraph(text=text, indent=0))
            continue
        level = _outline_level(line)
        if level is not None:
            # 개조식 항목 — 마커 그대로 두고 수준만큼 들여쓴 독립 문단으로 렌더.
            flush_para()
            blocks.append(Paragraph(text=_clean_inline(line), indent=level))
            continue
        para_buf.append(line)
    flush_para()
    flush_table()
    return blocks


def _drop_duplicate_lead_heading(md: str, chapter: int, section: int) -> str:
    """본문 첫 헤딩이 '# N.N …'로 섹션 제목을 반복하면 제거한다(제목 이중 인쇄 방지)."""
    lines = md.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        m = _HEADING_RE.match(line)
        if m and m.group(2).strip().startswith(f"{chapter}.{section}"):
            return "\n".join(lines[:i] + lines[i + 1 :])
        break
    return md


def _chapter_titles(state: ProjectState) -> dict[int, str]:
    """config.outline에서 챕터 제목을 위치 기반으로 복원(없으면 빈 dict)."""
    titles: dict[int, str] = {}
    outline = state.options.get("outline") if isinstance(state.options, dict) else None
    if isinstance(outline, dict):
        for i, ch in enumerate(outline.get("chapters") or [], start=1):
            if isinstance(ch, dict) and isinstance(ch.get("title"), str) and ch["title"].strip():
                titles[i] = ch["title"].strip()
    return titles


# 부제 길이 상한 — 넘치면 표지 한 장의 균형이 깨진다.
_SUBTITLE_MAX_CHARS = 120


def _report_type_label(preset: str | None) -> str:
    """표지 최상단 유형 라벨 — 프리셋 이름 기반. 자유 주제(프리셋 없음)면 빈 문자열."""
    name = (preset or "").strip()
    if not name:
        return ""
    return name if name.endswith("보고서") else f"{name} 보고서"


def _cover_subtitle(state: ProjectState) -> str:
    """부제 — 주제문(topic). 제목과 사실상 같으면 생략한다(같은 줄을 두 번 쓰지 않는다).

    topic은 "무엇을 어떤 범위로 검토한다"는 문장이라 표지 부제로 읽힌다. 제목이
    비어 topic이 이미 제목 자리에 올라간 경우에는 부제를 비운다.
    """
    topic = (state.topic or "").strip()
    title = (state.title or "").strip()
    if not topic or not title:
        return ""
    if topic == title or topic.startswith(title) or title.startswith(topic):
        return ""
    if len(topic) > _SUBTITLE_MAX_CHARS:
        # 긴 주제문은 앞부분이 제목을 되풀이하고 뒤가 실제 범위 서술이다
        # ("A 사업의 예타 분석 - 기술 수준, 시장 전망, …을 검토한다"). 뒤를 쓴다.
        for sep in (" - ", " – ", " — ", ": "):
            head, found, tail = topic.partition(sep)
            if found and len(tail.strip()) >= 15:
                topic = tail.strip()
                break
    return topic if len(topic) <= _SUBTITLE_MAX_CHARS else topic[: _SUBTITLE_MAX_CHARS - 1] + "…"


def _cover_block(state: ProjectState) -> Cover:
    """표지 블록 — 유형 라벨·제목·부제·작성일(KST)·기관·작성자."""
    created = state.created_at
    if created.tzinfo is None:  # 방어: naive면 UTC로 간주(저장은 UTC 규약)
        created = created.replace(tzinfo=UTC)
    date_text = created.astimezone(_KST).strftime("%Y년 %m월 %d일")
    # 표지 제목은 프로젝트 제목 — topic(작성 지시문)은 폴백일 뿐이다.
    return Cover(
        title=state.title or state.topic,
        organization=HEADER_TEXT,
        date_text=date_text,
        report_type=_report_type_label(state.preset),
        subtitle=_cover_subtitle(state),
        author=f"작성자  {state.author}" if state.author else "",
    )


def _chapter_heading_text(chapter_number: int, ch_titles: dict[int, str]) -> str:
    """장 헤딩 텍스트 — '제N장  제목'(제목 없으면 '제N장')."""
    ch_title = ch_titles.get(chapter_number)
    return f"제{chapter_number}장  {ch_title}" if ch_title else f"제{chapter_number}장"


def _figure_placeholder(plan: SectionPlan) -> Figure:
    """표가 없는 절에 넣을 추천 시각자료(그림) 자리표시자.

    실제 이미지를 못 구하므로, 절의 초점(key_points→direction→제목)을 근거로 어떤
    그림이 적합한지 설명을 적어 배치한다. 캡션 번호는 절 번호를 따른다.
    """
    if plan.key_points:
        focus = plan.key_points[0]
    elif plan.direction:
        focus = plan.direction
    else:
        focus = f"{plan.title}의 핵심 내용"
    caption = f"[그림 {plan.chapter_number}-{plan.section_number}] {plan.title}"
    description = f"{focus} 관련 핵심 수치·구조를 요약한 도표 또는 그래프"
    return Figure(caption=caption, description=description)


_SOURCE_TYPE_LABEL: dict[SourceType, str] = {
    SourceType.WEB_SEARCH: "웹",
    SourceType.UPLOAD: "업로드",
    SourceType.LIBRARY: "라이브러리",
}


# 최종장 제목 — 목차 항목과 장 헤딩이 같은 문자열을 쓰도록 한곳에서 고정한다.
REFERENCES_HEADING = "참고문헌"


def _source_entry(index: int, src: SourceRef) -> str:
    """참고문헌 목록 한 줄 — '[n] 제목 (유형) URL'."""
    line = f"[{index}] {src.title.strip() or '(제목 없음)'}"
    label = _SOURCE_TYPE_LABEL.get(src.source_type)
    if label:
        line += f" ({label})"
    if src.url:
        line += f" {src.url}"
    return line


def report_blocks(
    state: ProjectState, glossary: dict[str, dict[str, str]] | None = None
) -> list[Block]:
    """보고서 전체 블록을 실제 순서(표지 → 목차 → 본문 → 참고문헌)로 조립한다.

    - 1장: 표지(제목·기관·날짜).
    - 2장: 목차 — 렌더되는 장·절을 텍스트 번호로 나열(본문과 항상 일치).
    - 본문: 챕터 경계마다 쪽 나눔 + 장 헤딩, 섹션마다 [제목(2) + 개조식 본문].
      각 장 끝에는 그 장의 약어 정리를 **별도 페이지**(쪽 나눔 후)로 붙인다.
      glossary(조립 시 생성한 약어 사전)가 있으면 설명 열을 채운다.
    - 최종장: 참고문헌 — state.sources(채택 자료)를 번호 목록으로.
    선택 초안이 없는 섹션은 목차·본문 모두에서 건너뛴다.
    """
    drafts = {d.section_id: d for d in state.selected_drafts()}
    rendered = [plan for plan in state.section_plan if plan.section_id in drafts]

    blocks: list[Block] = [_cover_block(state)]
    if not rendered:
        return blocks

    ch_titles = _chapter_titles(state)
    # 인용 [n]은 전역 번호(참고문헌장과 일치)라 본문에 유지한다 — 제거하지 않는다(2026-08-05).
    contents = {plan.section_id: drafts[plan.section_id].content for plan in rendered}

    # 목차(2장) — 장 제목(indent 0) 아래 절(indent 1)을 계층으로 나열한다.
    blocks.append(PageBreak())
    blocks.append(Heading(level=1, text="목차"))
    toc_chapter: int | None = None
    for plan in rendered:
        if plan.chapter_number != toc_chapter:
            blocks.append(Paragraph(text=_chapter_heading_text(plan.chapter_number, ch_titles)))
            toc_chapter = plan.chapter_number
        entry = f"{plan.chapter_number}.{plan.section_number}  {plan.title}"
        blocks.append(Paragraph(text=entry, indent=1))
    # 참고문헌은 계획에 없는 최종장이라 목차에도 따로 실어야 본문과 어긋나지 않는다.
    if state.sources:
        blocks.append(Paragraph(text=REFERENCES_HEADING))

    # 본문 — 챕터마다 쪽 나눔 + 장 헤딩. 각 장의 약어는 그 장 끝에 "약어 정리"로 모으고,
    # 표가 없는 절에는 추천 시각자료(그림) 자리표시자를 넣어 페이지에 시각자료가 있게 한다.
    chapter_abbrs: dict[str, str] = {}
    current_chapter: int | None = None

    def flush_glossary() -> None:
        if chapter_abbrs:
            # 약어 정리는 장 끝 별도 페이지(쪽 나눔 후)에 배치한다(2026-08-05 확정).
            blocks.append(PageBreak())
            blocks.append(Heading(level=2, text="약어 정리"))
            rows = [
                [abbr, full, (glossary or {}).get(abbr, {}).get("desc", "")]
                for abbr, full in chapter_abbrs.items()
            ]
            blocks.append(Table(headers=["약어", "전체 명칭", "설명"], rows=rows))
            chapter_abbrs.clear()

    for plan in rendered:
        if plan.chapter_number != current_chapter:
            if current_chapter is not None:
                flush_glossary()  # 앞 장의 약어 정리를 그 장 끝에 배치
            blocks.append(PageBreak())  # 챕터는 항상 새 쪽에서 시작
            ch_text = _chapter_heading_text(plan.chapter_number, ch_titles)
            blocks.append(Heading(level=1, text=ch_text))
            current_chapter = plan.chapter_number
        title = f"{plan.chapter_number}.{plan.section_number} {plan.title}"
        blocks.append(Heading(level=2, text=title))
        content = _drop_duplicate_lead_heading(
            contents[plan.section_id], plan.chapter_number, plan.section_number
        )
        section_blocks = markdown_to_blocks(content)
        blocks.extend(section_blocks)
        if not any(isinstance(b, Table) for b in section_blocks):
            blocks.append(_figure_placeholder(plan))
        _collect_abbreviations(contents[plan.section_id], chapter_abbrs)
    flush_glossary()  # 마지막 장

    # 최종장: 참고문헌 — 프로젝트 자료 풀을 번호 목록으로 정리한다(자료 없으면 생략).
    if state.sources:
        blocks.append(PageBreak())
        blocks.append(Heading(level=1, text=REFERENCES_HEADING))
        for i, src in enumerate(state.sources, start=1):
            blocks.append(Paragraph(text=_source_entry(i, src), indent=1))
    return blocks


def export_report(
    state: ProjectState,
    *,
    output_dir: str | Path | None = None,
    template_path: str | Path | None = None,
    glossary: dict[str, dict[str, str]] | None = None,
) -> Path:
    """선택·조립된 보고서를 `<export_dir>/<project_id>.hwpx`로 렌더하고 경로를 반환.

    경로가 project_id로 결정적이라 별도 상태 기록 없이 다운로드 엔드포인트가
    같은 규칙으로 찾을 수 있다. 템플릿 미지정 시 회사 표준 서식을 코드로 적용.
    """
    out_dir = Path(output_dir) if output_dir is not None else Path(settings.export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    template: Path | None
    if template_path is not None:
        template = Path(template_path)
    elif settings.export_template_path:
        template = Path(settings.export_template_path)
    else:
        template = None

    path = out_dir / f"{state.project_id}.hwpx"
    build_report(
        report_blocks(state, glossary), path, template_path=template, apply_chrome=template is None
    )
    logger.info("export.hwpx_written", project_id=str(state.project_id), path=str(path))
    return path
