"""HWPX 보고서 출력기 — python-hwpx 기반(한컴 불필요).

전략: 한컴에서 만든 **마스터 템플릿**을 열어 본문만 채운다(template-fill).
템플릿이 없으면 빈 문서(`HwpxDocument.new()`)로 폴백한다 — PoC·개발용.

핵심 규칙 (시나리오 A: 페이지번호 값·PDF는 *받는 쪽 한컴*이 담당)
- 제목 문단에 **개요 수준(outline_level)** 을 부여한다 → 템플릿에 심어둔 자동 목차
  필드가 이 수준을 보고 항목을 수집한다.
- 쪽 번호는 `set_page_number` **자동 필드**로 꼬리말에 넣는다 → 받는 쪽 한컴이
  문서를 열어 렌더링할 때 실제 번호를 채운다.
- 회사 표준 서식(함초롬 폰트·여백·줄간격·머리말)을 적용한다.

이 모듈은 "레이아웃 결과(쪽 번호 값·페이지 나눔·PDF)"를 만들지 않는다.
그건 레이아웃 엔진(한컴)의 몫이며, 본 설계상 서버에는 한컴이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hwpx import HwpxDocument

# --- 회사 표준 양식 상수 (web ExportPage의 COMPANY_STYLE와 일치시킨다) ---
BODY_FONT = "함초롬바탕"
BODY_SIZE_PT = 11
HEADING_FONT = "함초롬돋움"
HEADING_SIZE_PT: dict[int, int] = {1: 16, 2: 14, 3: 12}
LINE_SPACING_PERCENT = 160
MARGIN_MM: dict[str, float] = {"top": 20.0, "bottom": 20.0, "left": 30.0, "right": 20.0}
HEADER_TEXT = "주식회사 로운인사이트"
MAX_HEADING_LEVEL = 3


@dataclass(frozen=True)
class Heading:
    """제목 블록. level 1=장, 2=절, 3=항 (자동 목차가 수집하는 개요 수준)."""

    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    """본문 문단 블록."""

    text: str


@dataclass(frozen=True)
class Table:
    """표 블록. 첫 행은 머리행(headers), 이후 rows."""

    headers: list[str]
    rows: list[list[str]]


Block = Heading | Paragraph | Table


def build_report(
    blocks: list[Block],
    output_path: str | Path,
    *,
    template_path: str | Path | None = None,
    apply_chrome: bool = True,
) -> Path:
    """`blocks`를 채운 HWPX를 `output_path`에 저장하고 그 경로를 반환한다.

    Args:
        blocks: 보고서 본문 구성 블록들.
        output_path: 저장할 .hwpx 경로.
        template_path: 한컴에서 만든 마스터 템플릿. 지정하면 그 템플릿을 열어 채운다
            (머리말/꼬리말/자동 목차 필드/제목 스타일은 템플릿이 보유). 미지정 시 빈 문서.
        apply_chrome: 머리말·쪽번호·여백을 코드로 설정할지. 템플릿이 이미 보유하면 False.
    """
    doc = HwpxDocument.open(str(template_path)) if template_path is not None else HwpxDocument.new()

    if apply_chrome:
        _apply_company_chrome(doc)

    for block in blocks:
        if isinstance(block, Heading):
            _add_heading(doc, block)
        elif isinstance(block, Paragraph):
            _add_body(doc, block.text)
        else:
            _add_table(doc, block)

    out = Path(output_path)
    doc.save_to_path(str(out))
    return out


def _apply_company_chrome(doc: HwpxDocument) -> None:
    """회사 표준 여백·머리말·자동 쪽번호를 설정한다."""
    doc.set_page_setup(
        margin_left_mm=MARGIN_MM["left"],
        margin_right_mm=MARGIN_MM["right"],
        margin_top_mm=MARGIN_MM["top"],
        margin_bottom_mm=MARGIN_MM["bottom"],
    )
    doc.set_header_text(HEADER_TEXT)
    # 자동 쪽 번호 필드 — 값은 받는 쪽 한컴이 렌더 시 채운다.
    doc.set_page_number(target="footer", format="page", align="CENTER")


def _add_heading(doc: HwpxDocument, heading: Heading) -> None:
    level = max(1, min(heading.level, MAX_HEADING_LEVEL))
    char_id = doc.ensure_run_style(font=HEADING_FONT, size=HEADING_SIZE_PT[level], bold=True)
    para = doc.add_paragraph(heading.text, char_pr_id_ref=char_id)
    idx = doc.paragraphs.index(para)
    # outline_level: 자동 목차 필드가 수집하는 개요 수준(한컴에서 최종 매핑 검증 필요).
    doc.set_paragraph_format(
        paragraph_index=idx,
        outline_level=level,
        line_spacing_percent=LINE_SPACING_PERCENT,
        spacing_before_pt=12.0,
        spacing_after_pt=6.0,
    )


def _add_body(doc: HwpxDocument, text: str) -> None:
    char_id = doc.ensure_run_style(font=BODY_FONT, size=BODY_SIZE_PT)
    para = doc.add_paragraph(text, char_pr_id_ref=char_id)
    idx = doc.paragraphs.index(para)
    doc.set_paragraph_format(paragraph_index=idx, line_spacing_percent=LINE_SPACING_PERCENT)


def _add_table(doc: HwpxDocument, table: Table) -> None:
    n_rows = len(table.rows) + 1
    n_cols = len(table.headers)
    tbl = doc.add_table(n_rows, n_cols)
    for col, head in enumerate(table.headers):
        tbl.set_cell_text(0, col, head)
    for row_idx, row in enumerate(table.rows, start=1):
        for col, value in enumerate(row):
            tbl.set_cell_text(row_idx, col, value)
