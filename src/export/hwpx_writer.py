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
from weakref import WeakKeyDictionary

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
# 개조식 글머리 문자 — 이 문자로 시작하는 문단은 마커 폭만큼 내어쓰기해 줄바꿈된 둘째
# 줄이 본문 글머리에 정렬되게 한다(_hanging_indent_mm).
OUTLINE_MARKERS: frozenset[str] = frozenset("□ㅇ○◦-*")
_MM_PER_PT = 25.4 / 72
# 개조식 문단 간격(pt) — 마커 항목이 붙어 보이지 않도록 문단마다 아래 여백을 준다.
# 대주제(□, indent 0)는 앞에도 큰 여백을 줘 논리 묶음을 시각적으로 분리한다.
BODY_SPACING_AFTER_PT = 3.0
GROUP_SPACING_BEFORE_PT = 9.0

# 표지 서식 — 제목은 크게, 기관/날짜는 보조. 개요 수준은 부여하지 않는다.
COVER_TITLE_SIZE_PT = 24
COVER_ORG_SIZE_PT = 14
COVER_DATE_SIZE_PT = 12
# 세로 배치용 여백(pt) — A4 본문 높이(≈728pt) 기준 제목을 화면 중앙 근처로 내린다.
COVER_SUBTITLE_SIZE_PT = 14
COVER_TYPE_SIZE_PT = 13
COVER_AUTHOR_SIZE_PT = 11
# 세로 배치(위→아래): 유형 라벨 → 제목 → 부제 → (여백) 작성일 → 기관 → 작성자.
# 한 페이지에 균형 있게 떨어지도록 앞 여백으로만 배치한다(표·프레임 없이).
COVER_TYPE_SPACE_BEFORE_PT = 110.0
COVER_ORG_SPACE_BEFORE_PT = 90.0  # 유형 라벨이 없을 때 제목 앞 여백
COVER_TITLE_SPACE_BEFORE_PT = 40.0
COVER_SUBTITLE_SPACE_BEFORE_PT = 14.0
COVER_DATE_SPACE_BEFORE_PT = 150.0
COVER_ORG_SPACE_AFTER_PT = 24.0

# 표·그림 폭 — A4(210mm) 기준 본문 폭(좌우 여백 제외)에 맞춰 페이지를 넘지 않게 한다.
PAGE_WIDTH_MM = 210.0
# 그림 플레이스홀더 캡션 음영(옅은 하늘색 느낌의 회색) — 데이터 표와 구분한다.
FIGURE_CAPTION_SHADE = "#DDE7F0"
# 표 제목 — 표 바로 위 한 줄. 본문보다 작고 굵게 써서 표에 딸린 라벨로 읽히게 한다.
TABLE_CAPTION_SIZE_PT = 10
# 표 부속 줄(단위·출처) — 캡션보다 한 단계 작게, 굵기 없이.
TABLE_NOTE_SIZE_PT = 9
# 차트 그림 높이(mm) — chart_render의 가로세로비(6.3:3.4)를 본문 폭 160mm에 맞춘 값.
CHART_HEIGHT_MM = 86.0
TABLE_CAPTION_SPACE_AFTER_PT = 1.0  # 표와 붙여 한 덩어리로 보이게 아래 여백을 최소로
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
    """표지 블록 — 문서 첫 장. 공공 보고서 표지의 표준 구성으로 배치한다.

    위에서부터: 유형 라벨(예 '예비타당성조사 보고서') → 제목(대) → 부제 →
    작성일 → 기관 · 작성자. 빈 값은 통째로 건너뛴다.
    개요 수준을 부여하지 않아 목차·개요 자동번호에 잡히지 않는다(표지는 본문이 아님).
    """

    title: str
    organization: str = ""
    date_text: str = ""
    report_type: str = ""  # 문서 유형 라벨 — 표지 최상단
    subtitle: str = ""  # 부제 — 제목 아래 한 단계 작게
    author: str = ""  # 작성자 — 기관명 아래


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
    """표 블록. 첫 행은 머리행(headers), 이후 rows.

    caption은 표 바로 위에 붙는 제목이다(예 "<표 2-1> 주요국 SMR 개발 현황"). 비어 있으면
    제목 없이 표만 렌더한다 — 약어 정리표처럼 이미 헤딩이 붙은 표가 그렇다.
    unit은 캡션과 표 사이 우측 정렬 한 줄(예 "(단위: 백만 원)"), source는 표 바로 아래
    한 줄(예 "출처: 과기정통부(2024), …") — 실측한 공공 보고서 관례(캡션 위·출처 아래)다.
    """

    headers: list[str]
    rows: list[list[str]]
    caption: str = ""
    unit: str = ""
    source: str = ""


@dataclass(frozen=True)
class Figure:
    """그림 플레이스홀더 — 실제 이미지 대신 '추천 시각자료 설명'을 담은 캡션 박스.

    자료 수집 단계에서 실제 이미지를 못 구하므로, 어떤 그림이 들어가면 좋을지
    설명을 적은 자리표시자를 페이지에 배치한다(작성 규칙의 2페이지당 시각자료 1개).
    """

    caption: str  # 예: "<그림 1-1> 원격경제 서비스 분류 체계"
    description: str  # 추천 시각자료 설명(무엇을, 어떤 형식으로)


@dataclass(frozen=True)
class Chart:
    """본문 차트 — 스펙을 PNG로 그려 그림으로 넣는다(자리표시자가 아닌 실제 그림).

    이미지 바이트는 렌더 시점에 만든다. 그리지 못하면(폰트 없음 등) 호출부가
    원본 표로 되돌리므로, 여기까지 온 차트는 그릴 수 있다고 본다.
    """

    spec: object  # src.core.charts.ChartSpec — 순환 import를 피해 느슨하게 받는다
    caption: str  # 예: "<그림 2-1> 주요국 SMR 투자 현황"


@dataclass(frozen=True)
class PageBreak:
    """쪽 나눔 — 다음 블록이 새 페이지에서 시작한다(챕터 경계 등)."""


Block = Cover | Heading | Paragraph | Table | Figure | Chart | PageBreak

# 표 머리행 음영(옅은 회색) — 본문 행과 시각적으로 구분한다.
TABLE_HEADER_SHADE = "#E6E6E6"

# 왼쪽 정렬 문단 속성 id — 문서마다 한 번만 만들어 표 셀에 재사용한다.
# 문서별로 보관한다: 백엔드는 한 프로세스에서 여러 보고서를 렌더하는데, 전역으로 두면
# 다른 문서에서 만든 id를 참조해 정렬이 엉뚱해진다.
_LEFT_PARA_IDS: WeakKeyDictionary = WeakKeyDictionary()


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
        elif isinstance(block, Chart):
            _add_chart(doc, block)
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
    """표지 문단들을 가운데 정렬·세로 배치로 추가한다(유형 → 제목 → 부제 → 날짜 → 기관·작성자)."""

    def line(text: str, *, size: float, bold: bool, space_before: float, font: str = HEADING_FONT):
        char_id = doc.ensure_run_style(font=font, size=size, bold=bold)
        para = doc.add_paragraph(text, char_pr_id_ref=char_id, inherit_style=False)
        doc.set_paragraph_format(
            paragraph_index=doc.paragraphs.index(para),
            alignment="CENTER",
            line_spacing_percent=150,
            spacing_before_pt=space_before,
        )

    if cover.report_type:
        line(
            cover.report_type,
            size=COVER_TYPE_SIZE_PT,
            bold=False,
            space_before=COVER_TYPE_SPACE_BEFORE_PT,
        )
    line(
        cover.title,
        size=COVER_TITLE_SIZE_PT,
        bold=True,
        space_before=COVER_TITLE_SPACE_BEFORE_PT
        if cover.report_type
        else COVER_ORG_SPACE_BEFORE_PT,
    )
    if cover.subtitle:
        line(
            cover.subtitle,
            size=COVER_SUBTITLE_SIZE_PT,
            bold=False,
            space_before=COVER_SUBTITLE_SPACE_BEFORE_PT,
        )
    if cover.date_text:
        line(
            cover.date_text,
            size=COVER_DATE_SIZE_PT,
            bold=False,
            space_before=COVER_DATE_SPACE_BEFORE_PT,
            font=BODY_FONT,
        )
    if cover.organization:
        line(
            cover.organization,
            size=COVER_ORG_SIZE_PT,
            bold=True,
            space_before=COVER_ORG_SPACE_AFTER_PT,
        )
    if cover.author:
        line(cover.author, size=COVER_AUTHOR_SIZE_PT, bold=False, space_before=6.0, font=BODY_FONT)


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
    # 마커 폭만큼 왼쪽 여백을 더 주고 첫 줄만 그만큼 당겨(음수) 내어쓰기를 만든다.
    # 이러면 항목이 두 줄 이상으로 넘어가도 둘째 줄이 마커 아래가 아니라 본문 글머리에
    # 맞춰 정렬돼, 마커 하나가 어디까지를 묶는지 눈으로 따라갈 수 있다(2026-08-11 지적).
    hanging = _hanging_indent_mm(text)
    if indent > 0 or hanging:
        # 개조식 수준만큼 왼쪽 여백을 줘 계층 들여쓰기를 표현한다.
        fmt["indent_left_mm"] = indent * OUTLINE_INDENT_MM + hanging
    if hanging:
        fmt["first_line_indent_mm"] = -hanging
    if indent == 0 or group_start:
        # 대주제(□)·서술 문단, 또는 새 ㅇ 그룹 시작 앞에 여백을 줘 논리 묶음 사이를 벌린다.
        fmt["spacing_before_pt"] = GROUP_SPACING_BEFORE_PT
    doc.set_paragraph_format(**fmt)
    return para


def _hanging_indent_mm(text: str) -> float:
    """개조식 마커와 뒤따르는 공백이 차지하는 폭(mm). 마커로 시작하지 않으면 0.

    이 폭이 곧 내어쓰기 양이다 — 왼쪽 여백에 더하고 첫 줄에서 같은 값을 빼면 마커만
    왼쪽으로 튀어나오고 본문은 한 줄로 이어져 보인다.
    """
    if len(text) < 2 or text[0] not in OUTLINE_MARKERS or text[1] != " ":
        return 0.0
    # _text_width는 반각을 1, 전각을 2로 세므로 반각 하나가 글자 크기의 절반에 해당한다.
    return _text_width(text[:2]) * (BODY_SIZE_PT * _MM_PER_PT / 2)


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
        # 도형 기호(□ ○ ◦ 등)는 유니코드상 East Asian Ambiguous지만 한글 폰트에서는
        # 전각으로 그려진다. 개조식 글머리로 쓰이므로 전각으로 세야 내어쓰기가 맞는다.
        or 0x25A0 <= o <= 0x25FF  # Geometric Shapes
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


def _left_align_para_id(doc: HwpxDocument) -> str | None:
    """왼쪽 정렬 문단 속성 id를 하나 만들어 재사용한다(표 셀에 붙일 용도).

    셀 문단은 기본 paraPr(양쪽 정렬)을 쓴다. 한글 표에서 양쪽 정렬은 짧은 셀의
    글자 사이를 벌려 놓아 읽기 나쁘다(2026-08-10 지적) — 왼쪽 정렬로 고정한다.
    """
    if doc in _LEFT_PARA_IDS:
        return _LEFT_PARA_IDS[doc]
    para_id: str | None = None
    try:
        probe = doc.add_paragraph("")
        result = doc.set_paragraph_format(
            paragraph_index=doc.paragraphs.index(probe), alignment="LEFT"
        )
        para_id = str(result["paragraphs"][0]["paraPrIDRef"])
        doc.remove_paragraph(probe)
    except Exception:  # noqa: BLE001 — 정렬은 표시 품질, 실패해도 표는 유효
        para_id = None
    _LEFT_PARA_IDS[doc] = para_id
    return para_id


def _align_cells_left(doc: HwpxDocument, tbl, n_rows: int, n_cols: int) -> None:
    para_id = _left_align_para_id(doc)
    if para_id is None:
        return
    for r in range(n_rows):
        for c in range(n_cols):
            try:
                for para in tbl.cell(r, c).paragraphs:
                    para.para_pr_id_ref = para_id
            except Exception:  # noqa: BLE001 — 병합 셀 등 접근 실패는 건너뛴다
                continue


def _add_table_caption(doc: HwpxDocument, caption: str) -> None:
    """표 제목 한 줄 — 표 바로 위에 붙인다(그림은 캡션이 박스 안, 표는 박스 밖)."""
    char_id = doc.ensure_run_style(font=HEADING_FONT, size=TABLE_CAPTION_SIZE_PT, bold=True)
    para = doc.add_paragraph(caption, char_pr_id_ref=char_id, inherit_style=False)
    doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(para),
        line_spacing_percent=LINE_SPACING_PERCENT,
        spacing_before_pt=GROUP_SPACING_BEFORE_PT,
        spacing_after_pt=TABLE_CAPTION_SPACE_AFTER_PT,
    )


def _add_table_note(doc: HwpxDocument, text: str, *, align: str, after_pt: float) -> None:
    """표 부속 한 줄 — 단위(표 위, 우측 정렬)·출처(표 아래, 좌측 정렬)에 쓴다."""
    char_id = doc.ensure_run_style(font=HEADING_FONT, size=TABLE_NOTE_SIZE_PT)
    para = doc.add_paragraph(text, char_pr_id_ref=char_id, inherit_style=False)
    doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(para),
        alignment=align,
        line_spacing_percent=LINE_SPACING_PERCENT,
        spacing_after_pt=after_pt,
    )


def _add_table(doc: HwpxDocument, table: Table) -> None:
    if table.caption:
        _add_table_caption(doc, table.caption)
    if table.unit:
        # 단위 줄은 실측 관례대로 캡션과 표 사이 우측 정렬로 붙인다.
        _add_table_note(doc, table.unit, align="RIGHT", after_pt=TABLE_CAPTION_SPACE_AFTER_PT)
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
    _align_cells_left(doc, tbl, n_rows, n_cols)
    if table.source:
        # 출처는 표 바로 아래 좌측 정렬 — 캡션 위·출처 아래가 실측 관례다.
        _add_table_note(doc, table.source, align="LEFT", after_pt=BODY_SPACING_AFTER_PT)


def _add_chart(doc: HwpxDocument, chart: Chart) -> None:
    """차트 — 캡션을 그림 위에 달고, 스펙을 PNG로 그려 가운데 정렬 그림으로 넣는다.

    캡션이 그림 **위**인 것은 실납품 보고서 실측 관례다(2026-08-11, 샘플 6종 중 5종이
    표·그림 모두 캡션 위 + 출처 아래).
    """
    from src.export.chart_render import render_png  # 지연 import — 렌더 시점에만 필요

    char_id = doc.ensure_run_style(font=HEADING_FONT, size=TABLE_CAPTION_SIZE_PT, bold=True)
    para = doc.add_paragraph(chart.caption, char_pr_id_ref=char_id, inherit_style=False)
    doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(para),
        alignment="CENTER",
        line_spacing_percent=LINE_SPACING_PERCENT,
        spacing_before_pt=GROUP_SPACING_BEFORE_PT,
        spacing_after_pt=TABLE_CAPTION_SPACE_AFTER_PT,
    )
    png = render_png(chart.spec)
    doc.add_picture(
        png,
        "png",
        width_mm=PAGE_WIDTH_MM - MARGIN_MM["left"] - MARGIN_MM["right"],
        height_mm=CHART_HEIGHT_MM,
        align="CENTER",
    )


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
