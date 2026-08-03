"""조립된 보고서 → HWPX 파일 — 파이프라인과 hwpx_writer 사이의 변환 계층.

선택된 섹션 초안(LLM이 쓴 마크다운)을 hwpx_writer의 Block 시퀀스로 바꿔 렌더한다.
실제 보고서 꼴을 갖추도록 **제목 → 목차 → 본문(장별)** 순으로 조립하며, 각 대(大)
섹션(챕터) 끝에 그 장에서 처음 등장한 영어 약어 정리표를 붙인다.

작성 규칙(src/prompts) 반영:
- 본문은 개조식(□ 대주제 → ㅇ → - → *)으로, 마커 수준마다 들여쓰기해 렌더한다.
- 약어는 "풀네임(약어)" 형식으로 첫 등장하므로(agent_writing_style.md), 이 패턴을
  장 단위로 수집해 장 말미에 "약어 정리"로 정리한다.
- 인용 마커 [n]은 검색 청크 인덱스라 최종 문서에서 무의미 → 제거한다
  (인용→출처 매핑은 SectionDraft.cited_chunk_ids에 보존, 출처 색인 부록은 향후).
- 제목 위계: 보고서 제목=1, 섹션 제목=2, 본문 내 마크다운 헤딩=3으로 클램프
  (hwpx_writer MAX_HEADING_LEVEL=3 — 자동 목차 필드가 이 개요 수준을 수집).
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from src.core.config import settings
from src.core.state import ProjectState
from src.export.hwpx_writer import Block, Heading, PageBreak, Paragraph, Table, build_report

logger = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\s*\[\d+\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")

# 본문 내 마크다운 헤딩이 렌더될 개요 수준 (섹션 제목=2 아래).
_CONTENT_HEADING_LEVEL = 3

# 개조식 마커 → 들여쓰기 수준. 값이 클수록 더 깊게 들여쓴다.
_OUTLINE_LEVEL_BY_MARKER: dict[str, int] = {"□": 0, "ㅇ": 1, "○": 1, "◦": 1, "-": 2, "*": 3}

# "풀네임(약어)" 첫 등장 표기 추출. 약어=대문자 시작 영문 2자 이상, 풀네임은 괄호 바로 앞의
# 영문 구(제목격 시작) 또는 공백 없는 한글 토큰(기관명 등). 괄호 앞 공백은 허용하지 않아
# "비율 (B/C)" 같은 비(非)약어 괄호 표기를 배제한다.
_ABBR_RE = re.compile(
    r"(?P<full>"
    r"[A-Z][A-Za-z0-9.&/\-]*(?:\s+[A-Za-z0-9.&/\-]+)*"  # 영문 구: "Small Modular Reactor"
    r"|[가-힣][가-힣A-Za-z0-9]*"  # 또는 한글 토큰: "한국개발연구원"
    r")\((?P<abbr>[A-Z][A-Za-z0-9&/\-]+)\)"
)
# 풀네임 병기가 생략되는 일반 상식 약어(agent_writing_style.md) — 정리표에서 제외.
_COMMON_ABBR: frozenset[str] = frozenset({"AI", "IT", "API", "GDP", "UN", "EU", "US", "UK", "OECD"})


def _strip_citations(text: str) -> str:
    """[n] 인용 마커 제거."""
    return _CITATION_RE.sub("", text)


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


def _clean_inline(text: str) -> str:
    """HWPX 평문 렌더에서 의미 없는 인라인 마크다운 기호 제거(강조·링크)."""
    text = text.replace("**", "")
    return _MD_LINK_RE.sub(r"\1", text)


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
            blocks.append(Table(headers=rows[0], rows=rows[1:]))

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


def report_blocks(state: ProjectState) -> list[Block]:
    """보고서 전체 블록을 실제 보고서 순서(제목 → 목차 → 본문)로 조립한다.

    본문은 plan 순서를 따르며 섹션마다 [제목(2) + 개조식 본문]을, 대(大) 섹션(챕터)이
    바뀔 때마다 그 장에서 처음 등장한 약어를 모은 "약어 정리"표를 앞 장 말미에 끼운다.
    선택 초안이 없는 섹션은 목차·본문 모두에서 건너뛴다.
    """
    drafts = {d.section_id: d for d in state.selected_drafts()}
    rendered = [plan for plan in state.section_plan if plan.section_id in drafts]

    blocks: list[Block] = [Heading(level=1, text=state.topic)]
    if not rendered:
        return blocks

    # 목차 — 렌더되는 섹션만 번호·제목으로 나열(본문과 항상 일치). 표지 다음 쪽에.
    blocks.append(PageBreak())
    blocks.append(Heading(level=1, text="목차"))
    for plan in rendered:
        entry = f"{plan.chapter_number}.{plan.section_number}  {plan.title}"
        blocks.append(Paragraph(text=entry, indent=1))

    # 본문 — 챕터 경계에서 앞 장의 약어 정리를 flush하고 새 쪽에서 시작한다.
    chapter_abbrs: dict[str, str] = {}
    current_chapter: int | None = None

    def flush_glossary() -> None:
        if chapter_abbrs:
            rows = [[abbr, full] for abbr, full in chapter_abbrs.items()]
            blocks.append(Heading(level=3, text="약어 정리"))
            blocks.append(Table(headers=["약어", "전체 명칭"], rows=rows))
            chapter_abbrs.clear()

    ch_titles = _chapter_titles(state)
    for plan in rendered:
        if plan.chapter_number != current_chapter:
            if current_chapter is not None:
                flush_glossary()
            blocks.append(PageBreak())  # 챕터는 항상 새 쪽에서 시작
            ch_title = ch_titles.get(plan.chapter_number)
            chapter_text = (
                f"제{plan.chapter_number}장 {ch_title}"
                if ch_title
                else f"제{plan.chapter_number}장"
            )
            blocks.append(Heading(level=1, text=chapter_text))
        current_chapter = plan.chapter_number
        title = f"{plan.chapter_number}.{plan.section_number} {plan.title}"
        blocks.append(Heading(level=2, text=title))
        content = _strip_citations(drafts[plan.section_id].content)
        content = _drop_duplicate_lead_heading(content, plan.chapter_number, plan.section_number)
        blocks.extend(markdown_to_blocks(content))
        _collect_abbreviations(content, chapter_abbrs)
    flush_glossary()
    return blocks


def export_report(
    state: ProjectState,
    *,
    output_dir: str | Path | None = None,
    template_path: str | Path | None = None,
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
    build_report(report_blocks(state), path, template_path=template, apply_chrome=template is None)
    logger.info("export.hwpx_written", project_id=str(state.project_id), path=str(path))
    return path
