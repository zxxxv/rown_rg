"""조립된 보고서 → HWPX 파일 — 파이프라인과 hwpx_writer 사이의 변환 계층.

선택된 섹션 초안(LLM이 쓴 마크다운)을 hwpx_writer의 Block 시퀀스로 바꿔 렌더한다.

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
from src.export.hwpx_writer import Block, Heading, Paragraph, Table, build_report

logger = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\s*\[\d+\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")

# 본문 내 마크다운 헤딩이 렌더될 개요 수준 (섹션 제목=2 아래).
_CONTENT_HEADING_LEVEL = 3


def _strip_citations(text: str) -> str:
    """[n] 인용 마커 제거."""
    return _CITATION_RE.sub("", text)


def _clean_inline(text: str) -> str:
    """HWPX 평문 렌더에서 의미 없는 인라인 마크다운 강조 기호 제거."""
    return text.replace("**", "")


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(_TABLE_SEP_CELL_RE.match(c) for c in cells if c) and any(cells)


def markdown_to_blocks(md: str) -> list[Block]:
    """마크다운 본문을 Heading/Paragraph/Table 블록으로 변환.

    지원 범위는 LLM 산출물에서 실제로 나오는 형태(헤딩·문단·불릿·GFM 표)로 한정한
    보수적 파서다. 인식 못 하는 줄은 문단으로 강등된다(내용 유실 없음).
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
        if line.startswith("|"):
            flush_para()
            table_buf.append(_table_cells(line))
            continue
        flush_table()
        heading = _HEADING_RE.match(line)
        if heading:
            flush_para()
            blocks.append(
                Heading(level=_CONTENT_HEADING_LEVEL, text=_clean_inline(heading.group(2)).strip())
            )
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_para()
            blocks.append(Paragraph(text="· " + _clean_inline(bullet.group(1))))
            continue
        para_buf.append(line)
    flush_para()
    flush_table()
    return blocks


def report_blocks(state: ProjectState) -> list[Block]:
    """보고서 전체 블록: 제목(1) + 섹션별 [제목(2) + 본문 블록들]. plan 순서를 따른다."""
    blocks: list[Block] = [Heading(level=1, text=state.topic)]
    drafts = {d.section_id: d for d in state.selected_drafts()}
    for plan in state.section_plan:
        draft = drafts.get(plan.section_id)
        if draft is None:
            continue
        title = f"{plan.chapter_number}.{plan.section_number} {plan.title}"
        blocks.append(Heading(level=2, text=title))
        blocks.extend(markdown_to_blocks(_strip_citations(draft.content)))
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
