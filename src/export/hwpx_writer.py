"""HWPX 보고서 출력기 — python-hwpx 기반(한컴 불필요).

전략: 한컴에서 만든 **마스터 템플릿**을 열어 본문만 채운다(template-fill).
템플릿이 없으면 빈 문서(`HwpxDocument.new()`)로 폴백한다 — PoC·개발용.

핵심 규칙 (시나리오 A: 페이지번호 값·PDF는 *받는 쪽 한컴*이 담당)
- 문서 첫 장은 **표지(Cover)** — 제목·기관·날짜를 가운데 정렬로 배치한다.
- 제목/장/절 번호는 **본문 텍스트에 직접** 넣고("제1장", "1.1 …"), 목차도 코드가
  직접 조립한다. 헤딩에 개요 자동번호(outline)를 부여하지 **않는다** — 부여하면
  한컴이 "1. 제목", "2. 목차"처럼 원치 않는 개요 번호를 앞에 붙이기 때문이다.
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
# 개조식 한 수준당 왼쪽 들여쓰기(mm). Paragraph.indent 0=□, 1=ㅇ, 2=-, 3=* 순으로 누적된다.
OUTLINE_INDENT_MM = 5.0
# 개조식 문단 간격(pt) — 마커 항목이 붙어 보이지 않도록 문단마다 아래 여백을 준다.
# 대주제(□, indent 0)는 앞에도 큰 여백을 줘 논리 묶음을 시각적으로 분리한다.
BODY_SPACING_AFTER_PT = 3.0
GROUP_SPACING_BEFORE_PT = 9.0

# 표지 서식 — 제목은 크게, 기관/날짜는 보조. 개요 수준은 부여하지 않는다.
COVER_TITLE_SIZE_PT = 24
COVER_ORG_SIZE_PT = 14
COVER_DATE_SIZE_PT = 12
# 세로 배치용 여백(pt) — A4 본문 높이(≈728pt) 기준 제목을 화면 중앙 근처로 내린다.
COVER_ORG_SPACE_BEFORE_PT = 90.0
COVER_TITLE_SPACE_BEFORE_PT = 150.0
COVER_DATE_SPACE_BEFORE_PT = 220.0

# 표·그림 폭 — A4(210mm) 기준 본문 폭(좌우 여백 제외)에 맞춰 페이지를 넘지 않게 한다.
PAGE_WIDTH_MM = 210.0
# 그림 플레이스홀더 캡션 음영(옅은 하늘색 느낌의 회색) — 데이터 표와 구분한다.
FIGURE_CAPTION_SHADE = "#DDE7F0"
# 표 열 폭 배분 시 한 열의 상대 가중치 하한/상한 — 극단적으로 좁거나 넓은 열을 막는다.
COL_WEIGHT_MIN = 8
COL_WEIGHT_MAX = 60

_HWPUNIT_PER_MM = 7200.0 / 25.4  # 1 inch = 7200 HWPUNIT = 25.4mm


def _mm_to_hwpunit(mm: float) -> int:
    return int(round(mm * _HWPUNIT_PER_MM))


def _page_content_width_hwp() -> int:
    """본문 가용 폭(mm→HWPUNIT). 표/그림은 이 폭을 넘지 않는다."""
    return _mm_to_hwpunit(PAGE_WIDTH_MM - MARGIN_MM["left"] - MARGIN_MM["right"])


@dataclass(frozen=True)
class Cover:
    """표지 블록 — 문서 첫 장. 제목(대)·기관·날짜를 가운데 정렬로 배치한다.

    개요 수준을 부여하지 않아 목차·개요 자동번호에 잡히지 않는다(표지는 본문이 아님).
    """

    title: str
    organization: str = ""
    date_text: str = ""


@dataclass(frozen=True)
class Heading:
    """제목 블록. level 1=장, 2=절, 3=항 (글자 크기 단계). 개요 자동번호는 부여하지 않는다."""

    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    """본문 문단 블록. indent=개조식 들여쓰기 수준(0=□ 대주제, 1=ㅇ, 2=-, 3=*)."""

    text: str
    indent: int = 0


@dataclass(frozen=True)
class Table:
    """표 블록. 첫 행은 머리행(headers), 이후 rows."""

    headers: list[str]
    rows: list[list[str]]


@dataclass(frozen=True)
class Figure:
    """그림 플레이스홀더 — 실제 이미지 대신 '추천 시각자료 설명'을 담은 캡션 박스.

    자료 수집 단계에서 실제 이미지를 못 구하므로, 어떤 그림이 들어가면 좋을지
    설명을 적은 자리표시자를 페이지에 배치한다(작성 규칙의 2페이지당 시각자료 1개).
    """

    caption: str  # 예: "[그림 1-1] 원격경제 서비스 분류 체계"
    description: str  # 추천 시각자료 설명(무엇을, 어떤 형식으로)


@dataclass(frozen=True)
class PageBreak:
    """쪽 나눔 — 다음 블록이 새 페이지에서 시작한다(챕터 경계 등)."""


Block = Cover | Heading | Paragraph | Table | Figure | PageBreak

# 표 머리행 음영(옅은 회색) — 본문 행과 시각적으로 구분한다.
TABLE_HEADER_SHADE = "#E6E6E6"


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

    pending_page_break = False
    # 직전 개조식 문단의 들여쓰기 수준 — 하위 항목(- *) 뒤에 새 ㅇ가 오면 새 그룹 시작으로 본다.
    prev_indent: int | None = None
    for block in blocks:
        if isinstance(block, PageBreak):
            # 실제 나눔은 '다음 문단'의 pageBreak 속성으로 표현된다(HWPX 규격).
            pending_page_break = True
            continue
        if isinstance(block, Cover):
            _add_cover(doc, block)
            para = None
            prev_indent = None
        elif isinstance(block, Heading):
            para = _add_heading(doc, block)
            prev_indent = None
        elif isinstance(block, Paragraph):
            # ㅇ(1)이 하위 항목(- *, 2↑) 뒤에 오면 새 그룹 시작으로 봐 앞 간격을 준다
            # (□ 없이 ㅇ만 쓴 문단도 묶음이 벌어지게). □(0)은 _add_body가 늘 그룹 처리.
            group_start = _is_group_start(block.indent, prev_indent)
            para = _add_body(doc, block.text, block.indent, group_start=group_start)
            prev_indent = block.indent
        elif isinstance(block, Figure):
            _add_figure(doc, block)
            para = None
            prev_indent = None
        else:
            _add_table(doc, block)
            para = None
            prev_indent = None
        if pending_page_break and para is not None:
            element = getattr(para, "element", None)
            if element is not None:
                element.set("pageBreak", "1")
            pending_page_break = False

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


def _add_cover(doc: HwpxDocument, cover: Cover) -> None:
    """표지 문단들(기관 → 제목 → 날짜)을 가운데 정렬·세로 배치로 추가한다."""
    if cover.organization:
        org_id = doc.ensure_run_style(font=HEADING_FONT, size=COVER_ORG_SIZE_PT)
        org = doc.add_paragraph(cover.organization, char_pr_id_ref=org_id, inherit_style=False)
        doc.set_paragraph_format(
            paragraph_index=doc.paragraphs.index(org),
            alignment="CENTER",
            spacing_before_pt=COVER_ORG_SPACE_BEFORE_PT,
        )
    title_id = doc.ensure_run_style(font=HEADING_FONT, size=COVER_TITLE_SIZE_PT, bold=True)
    title = doc.add_paragraph(cover.title, char_pr_id_ref=title_id, inherit_style=False)
    doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(title),
        alignment="CENTER",
        line_spacing_percent=150,
        spacing_before_pt=COVER_TITLE_SPACE_BEFORE_PT,
    )
    if cover.date_text:
        date_id = doc.ensure_run_style(font=BODY_FONT, size=COVER_DATE_SIZE_PT)
        date = doc.add_paragraph(cover.date_text, char_pr_id_ref=date_id, inherit_style=False)
        doc.set_paragraph_format(
            paragraph_index=doc.paragraphs.index(date),
            alignment="CENTER",
            spacing_before_pt=COVER_DATE_SPACE_BEFORE_PT,
        )


def _add_heading(doc: HwpxDocument, heading: Heading):
    level = max(1, min(heading.level, MAX_HEADING_LEVEL))
    char_id = doc.ensure_run_style(font=HEADING_FONT, size=HEADING_SIZE_PT[level], bold=True)
    # inherit_style=False: 앞 문단의 paraPr를 물려받지 않고 깨끗하게 시작한다.
    para = doc.add_paragraph(heading.text, char_pr_id_ref=char_id, inherit_style=False)
    idx = doc.paragraphs.index(para)
    # 개요 수준(outline_level)은 부여하지 않는다 — 부여하면 한컴이 "1.", "2." 개요 자동번호를
    # 헤딩 앞에 붙인다. 번호는 헤딩 텍스트("제1장", "1.1 …")에 이미 들어 있다.
    doc.set_paragraph_format(
        paragraph_index=idx,
        line_spacing_percent=LINE_SPACING_PERCENT,
        spacing_before_pt=12.0,
        spacing_after_pt=6.0,
    )
    return para


def _is_group_start(indent: int, prev_indent: int | None) -> bool:
    """ㅇ(1)이 하위 항목(- *, 2↑) 뒤에 와 새 논리 묶음을 여는지.

    □(0)은 여기서 다루지 않는다(_add_body가 indent==0을 늘 그룹 시작으로 처리).
    직전이 없거나(문단 시작·헤딩 직후) 같은/상위 수준이면 새 그룹이 아니다.
    """
    return indent == 1 and prev_indent is not None and prev_indent >= 2


def _add_body(doc: HwpxDocument, text: str, indent: int = 0, group_start: bool = False):
    char_id = doc.ensure_run_style(font=BODY_FONT, size=BODY_SIZE_PT)
    para = doc.add_paragraph(text, char_pr_id_ref=char_id, inherit_style=False)
    idx = doc.paragraphs.index(para)
    fmt: dict[str, float | int] = {
        "paragraph_index": idx,
        "line_spacing_percent": LINE_SPACING_PERCENT,
        # 문단마다 아래 여백을 줘 개조식 항목들이 붙어 보이지 않게 한다(가독성).
        "spacing_after_pt": BODY_SPACING_AFTER_PT,
    }
    if indent > 0:
        # 개조식 수준만큼 왼쪽 여백을 줘 계층 들여쓰기를 표현한다.
        fmt["indent_left_mm"] = indent * OUTLINE_INDENT_MM
    if indent == 0 or group_start:
        # 대주제(□)·서술 문단, 또는 새 ㅇ 그룹 시작 앞에 여백을 줘 논리 묶음 사이를 벌린다.
        fmt["spacing_before_pt"] = GROUP_SPACING_BEFORE_PT
    doc.set_paragraph_format(**fmt)
    return para


def _text_width(text: str) -> int:
    """문자열의 대략적 표시 폭 — 한중일 문자는 2, 그 외는 1로 근사한다."""
    return sum(2 if _is_wide(ch) else 1 for ch in text)


def _is_wide(ch: str) -> bool:
    o = ord(ch)
    # 한글/한자/가나/전각 기호 대역(근사) — 폭 계산용.
    return (
        0x1100 <= o <= 0x115F  # Hangul Jamo
        or 0x2E80 <= o <= 0xA4CF  # CJK 부수·한자·가나·한글 호환 등
        or 0xAC00 <= o <= 0xD7A3  # Hangul Syllables
        or 0xF900 <= o <= 0xFAFF  # CJK 호환 한자
        or 0xFF00 <= o <= 0xFF60  # 전각 영숫자·기호
    )


def _column_weights(headers: list[str], rows: list[list[str]]) -> list[int]:
    """열별 상대 폭 가중치 — 각 열의 (머리행/평균 본문) 표시 폭을 반영해 배분한다.

    긴 내용 열은 넓게, 짧은 라벨 열은 좁게 잡아 셀이 페이지를 넘지 않고 자연스럽게
    줄바꿈되도록 한다. 극단값은 하한/상한으로 클램프해 특정 열이 너무 좁아지지 않게 한다.
    """
    n = len(headers)
    weights: list[int] = []
    for c in range(n):
        widths = [_text_width(str(headers[c]))]
        widths += [_text_width(str(row[c])) for row in rows if c < len(row)]
        # 머리행 폭과 본문 평균 폭 중 큰 값 — 헤더가 잘리지 않으면서 본문 비중도 반영.
        body_avg = sum(widths[1:]) / len(widths[1:]) if len(widths) > 1 else widths[0]
        weight = max(widths[0], round(body_avg))
        weights.append(min(max(weight, COL_WEIGHT_MIN), COL_WEIGHT_MAX))
    return weights


def _fit_table_width(tbl, headers: list[str], rows: list[list[str]]) -> None:
    """표 전체 폭을 본문 폭에 맞추고 열 폭을 내용 비례로 배분한다(페이지 넘침 방지)."""
    try:
        tbl.set_column_widths(_column_weights(headers, rows))
    except Exception:  # noqa: BLE001 — 폭 배분 실패해도 표 자체는 유효
        pass


def _add_table(doc: HwpxDocument, table: Table) -> None:
    n_rows = len(table.rows) + 1
    n_cols = len(table.headers)
    # width=본문 폭: 표가 페이지를 넘지 않게 하고, 긴 셀은 열 폭 안에서 줄바꿈된다.
    tbl = doc.add_table(n_rows, n_cols, width=_page_content_width_hwp())
    for col, head in enumerate(table.headers):
        tbl.set_cell_text(0, col, head)
        try:
            # 머리행 음영 — API 버전에 따라 없을 수 있어 방어적으로 시도한다.
            tbl.set_cell_shading(0, col, TABLE_HEADER_SHADE)
        except Exception:  # noqa: BLE001 — 음영은 장식, 실패해도 표는 유효
            pass
    for row_idx, row in enumerate(table.rows, start=1):
        for col, value in enumerate(row):
            tbl.set_cell_text(row_idx, col, value)
    _fit_table_width(tbl, table.headers, table.rows)


def _add_figure(doc: HwpxDocument, figure: Figure) -> None:
    """그림 플레이스홀더 — 캡션(음영) + 추천 시각자료 설명을 담은 1열 박스로 렌더한다."""
    note = "※ 실제 이미지는 확정 후 삽입되는 자리표시자입니다."
    tbl = doc.add_table(3, 1, width=_page_content_width_hwp())
    tbl.set_cell_text(0, 0, figure.caption)
    tbl.set_cell_text(1, 0, f"추천 시각자료: {figure.description}")
    tbl.set_cell_text(2, 0, note)
    try:
        tbl.set_cell_shading(0, 0, FIGURE_CAPTION_SHADE)  # 캡션 행만 음영 — 데이터 표와 구분
    except Exception:  # noqa: BLE001 — 음영은 장식, 실패해도 박스는 유효
        pass
