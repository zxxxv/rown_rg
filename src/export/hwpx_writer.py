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

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

import structlog
from hwpx import HwpxDocument

from src.export.hwpx_fields import (
    append_leader_tab,
    append_page_ref,
    ensure_toc_tab_pr,
    set_tab_pr,
    wrap_bookmark,
)

logger = structlog.get_logger(__name__)

# --- 회사 표준 양식 상수 (web ExportPage의 COMPANY_STYLE와 일치시킨다) ---
BODY_FONT = "함초롬바탕"
# 본문 크기 — 실납품 알키미스트 본문이 13pt(KoPub바탕체 Light, 17,486자로 압도적
# 최빈)라 종전 11pt는 관례보다 작았다(2026-08-24 실측). 글꼴이 다르면 같은 pt라도
# 체감이 달라 12pt로 올린다. 글꼴은 함초롬 유지 — KoPub은 열람 PC에 없으면 대체
# 글꼴로 깨진다(한컴 기본 탑재인 함초롬이 안전).
BODY_SIZE_PT = 12
HEADING_FONT = "함초롬돋움"
HEADING_SIZE_PT: dict[int, int] = {1: 16, 2: 14, 3: 12}
# 본문 줄간격 — 130%를 실납품 최빈값으로 한 번 내렸다가 눈으로 보고 160%로 되돌렸다
# (2026-08-24 사용자 확정). 표 안은 좁아야 읽히므로 따로 130%를 쓴다.
LINE_SPACING_PERCENT = 160
CELL_LINE_SPACING_PERCENT = 130
# 본문 정렬 — 실납품 6종 전부 양쪽 정렬이 주력이다(2026-08-24 실측: 60~94%, 평균
# 82%). 낱말 벌어짐 때문에 왼쪽 정렬로 한 번 바꿨다가 되돌렸다 — 벌어짐의 원인은
# 정렬 방식이 아니라 최소 공백 비율이 0이었던 것이다(_apply_min_space_ratio).
BODY_ALIGNMENT = "JUSTIFY"
# 최소 공백 비율(%) — 양쪽 정렬에서 공백을 이만큼까지 압축해 어절을 끌어올 수 있다.
# 실납품 알키미스트 본문 83%가 25%(우리는 0%였다).
MIN_SPACE_RATIO_PERCENT = 25
# 실납품 2종 실측 일치(2026-08-24, 알키미스트 hwpx·비수도권 hwp): 좌우 20·상하 15.
# 종전 좌30(한글 기본값 잔재)·상하20은 본문을 오른쪽으로 밀어 개조식이 깊어 보였다.
MARGIN_MM: dict[str, float] = {"top": 15.0, "bottom": 15.0, "left": 20.0, "right": 20.0}
HEADER_TEXT = "주식회사 로운인사이트"
MAX_HEADING_LEVEL = 3
_MM_PER_PT = 25.4 / 72
# 개조식 계단(pt) — 글머리 첫 줄 시작 = (수준+1)×4pt: □4·ㅇ8·-12·*16 (2026-08-24 지시
# "이렇게 당겨"). 종전 수준당 전각 한 칸(11pt)은 사다리가 깊었다.
OUTLINE_STEP_PT = 4.0
# 마커가 차지하는 칸 폭(반각 칸) — 전각 마커+공백("□ "·"ㅇ ")이 3칸이라 그것에 맞춘다.
# 좁은 마커("- "·"* ", 2칸)도 같은 칸을 쓰도록 공백으로 채운다: 칸 폭이 마커마다
# 다르면 본문 시작점이 계단을 거슬러 역전된다(2026-08-24 실사고: ㅇ 24.5pt인데
# 그 아래 '-'가 23.0pt로 더 왼쪽에 섰다).
OUTLINE_SLOT_HALF = 3
# 개조식 글머리 문자 — 이 문자로 시작하는 문단은 마커 폭만큼 내어쓰기해 줄바꿈된 둘째
# 줄이 본문 글머리에 정렬되게 한다(_hanging_indent_mm).
OUTLINE_MARKERS: frozenset[str] = frozenset("□ㅇ○◦-*")
# 개조식 문단 간격(pt) — 마커 항목이 붙어 보이지 않도록 문단마다 아래 여백을 준다.
# 대주제(□, indent 0)는 앞에도 큰 여백을 줘 논리 묶음을 시각적으로 분리한다.
BODY_SPACING_AFTER_PT = 3.0
GROUP_SPACING_BEFORE_PT = 9.0
# 목차 줄에서 쪽번호 몫으로 비워 두는 오른쪽 자리(mm).
# 이 여백만큼 글자가 일찍 접히므로, 제목이 길어 두 줄이 되더라도 마지막 줄 끝에 점선과
# 번호가 들어갈 자리가 남는다. 없으면 제목이 오른쪽 끝까지 차서 번호만 다음 줄로 밀린다
# (실측: 이 보고서 목차 276줄 중 3줄이 여백 2mm 안쪽까지 차 있었다).
TOC_NUMBER_RESERVE_MM = 8.0

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
# 표·그림 제목 — 굵게 12pt·가운데(2026-08-24 지시. 표는 표 위, 그림은 그림 아래).
TABLE_CAPTION_SIZE_PT = 12
# 표 부속 줄(단위·출처) — 캡션보다 작게, 굵기 없이.
TABLE_NOTE_SIZE_PT = 9
# 표 아래 출처 줄 들여쓰기 — 개조식 계단 한 단과 같은 값(2026-08-24 지시).
SOURCE_NOTE_INDENT_MM = OUTLINE_STEP_PT * _MM_PER_PT
# 표 안 문단 위 간격(pt) — 9→1 (2026-08-24 지시).
CELL_PARA_SPACING_BEFORE_PT = 1.0
# 차트 그림 높이(mm) — chart_render의 가로세로비(6.3:3.4)를 본문 폭 170mm에 맞춘 값.
CHART_HEIGHT_MM = 92.0
# 그림 자리표시자 박스 높이(mm) — 실제 그림이 들어갈 자리만큼 잡아 둔다(차트와 같은 몫).
# 마지막 줄(안내 문구)만 낮게 두고 나머지를 설명 칸이 갖는다.
FIGURE_BOX_HEIGHT_MM = 90.0
FIGURE_NOTE_ROW_MM = 8.0
# 자리표시자에 싣는 원본 자료 수 — 더 실으면 안내 줄이 그림 자리를 먹는다.
_FIGURE_HINT_MAX = 2
TABLE_CAPTION_SPACE_AFTER_PT = 1.0  # 표와 붙여 한 덩어리로 보이게 여백을 최소로
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
    """제목 블록. level 1=장, 2=절, 3=항 (글자 크기 단계). 개요 자동번호는 부여하지 않는다.

    bookmark를 주면 제목 글자를 책갈피로 감싼다 — 목차 줄의 쪽번호 필드가 가리킬 표적이다.
    """

    level: int
    text: str
    # 목차와 본문을 잇는 이름일 뿐 내용이 아니다 — 블록 비교에서는 뺀다(Chart.caption과 같은 이유).
    bookmark: str = field(default="", compare=False)


@dataclass(frozen=True)
class Paragraph:
    """본문 문단 블록. indent=개조식 들여쓰기 수준(0=□ 대주제, 1=ㅇ, 2=-, 3=*).

    page_ref를 주면 목차 줄로 본다 — 오른쪽 끝까지 점선 탭을 깔고 그 책갈피의
    쪽번호 필드를 붙인다(값은 문서를 여는 한컴이 채운다).
    """

    text: str
    indent: int = 0
    page_ref: str = field(default="", compare=False)
    # 정렬 지정("LEFT" 등). 기본은 문서 기본값(양쪽 정렬)이다 — 줄바꿈이 불가능한 긴
    # 토큰(URL 등)이 있는 줄은 양쪽 정렬이 앞줄을 늘려 놓으므로 왼쪽 정렬로 지정한다.
    align: str = field(default="", compare=False)


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
    # 표 목차 줄이 가리킬 이름. 내용이 아니라 연결용이라 블록 비교에서는 뺀다.
    caption_bookmark: str = field(default="", compare=False)
    # 열 폭 비율을 직접 지정한다(미지정 시 내용 비례로 자동 배분). 같은 성격의 표가
    # 여러 개로 나뉠 때 열 폭이 표마다 달라지지 않게 고정하는 용도다.
    column_weights: list[int] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Figure:
    """그림 플레이스홀더 — 실제 이미지 대신 '추천 시각자료 설명'을 담은 캡션 박스.

    자료 수집 단계에서 실제 이미지를 못 구하므로, 어떤 그림이 들어가면 좋을지
    설명을 적은 자리표시자를 페이지에 배치한다(작성 규칙의 2페이지당 시각자료 1개).
    """

    caption: str  # 예: "<그림 1-1> 원격경제 서비스 분류 체계"
    description: str  # 추천 시각자료 설명(무엇을, 어떤 형식으로)
    # 그 절이 근거로 쓴 자료 — 원본 그림을 찾아갈 자리다. "따라가서 보고 다시 그릴지
    # 따다 쓸지 판단하게" 하려면 자리표시자가 출처를 데리고 있어야 한다(2026-08-24 지시).
    source_hints: list[str] = field(default_factory=list, compare=False)
    caption_bookmark: str = field(default="", compare=False)


@dataclass(frozen=True)
class Chart:
    """본문 차트 — 스펙을 PNG로 그려 그림으로 넣는다(자리표시자가 아닌 실제 그림).

    이미지 바이트는 렌더 시점에 만든다. 그때 실패할 수 있으므로(운영 컨테이너에 한글
    폰트가 없으면 라벨이 깨진 그림 대신 ChartRenderError가 온다) ``fallback``에 원본 표를
    함께 들려 보낸다 — 그림 하나를 못 그렸다고 보고서 전체가 안 나오면 안 된다.
    """

    spec: object  # src.core.charts.ChartSpec — 순환 import를 피해 느슨하게 받는다
    caption: str  # 예: "<그림 2-1> 주요국 SMR 투자 현황"
    caption_bookmark: str = field(default="", compare=False)
    # 못 그렸을 때 대신 실을 원본 표. 스펙에서 파생되는 값이라 블록 비교에서는 뺀다.
    fallback: Table | None = field(default=None, compare=False)


@dataclass(frozen=True)
class PageBreak:
    """쪽 나눔 — 다음 블록이 새 페이지에서 시작한다(챕터 경계 등)."""


Block = Cover | Heading | Paragraph | Table | Figure | Chart | PageBreak

# 셀 안 여백(HWPUNIT) — 한컴이 표를 새로 넣을 때 쓰는 기본값 그대로다(실납품 샘플
# 342개 셀에서 left/right=510, top/bottom=141로 실측). python-hwpx는 이 값을 0으로
# 만들어 글자가 괘선에 붙는다(2026-08-20 지적) → 표를 만들 때마다 되돌려 준다.
CELL_MARGIN_SIDE_HWP = 510  # 1.8mm
CELL_MARGIN_VERT_HWP = 141  # 0.5mm

# 표 셀 문단 속성 id — 문서마다 한 번만 만들어 재사용한다.
# 문서별로 보관한다: 백엔드는 한 프로세스에서 여러 보고서를 렌더하는데, 전역으로 두면
# 다른 문서에서 만든 id를 참조해 정렬이 엉뚱해진다.
_CELL_PARA_IDS: WeakKeyDictionary = WeakKeyDictionary()


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
            para = _add_body(
                doc, block.text, block.indent, group_start=group_start, align=block.align
            )
            if block.page_ref:
                _toc_page_ref(doc, para, block.text, block.page_ref)
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

    _apply_min_space_ratio(doc)
    out = Path(output_path)
    doc.save_to_path(str(out))
    return out


def _apply_min_space_ratio(doc: HwpxDocument) -> None:
    """모든 문단 모양에 최소 공백 비율을 넣는다 — 양쪽 정렬의 낱말 벌어짐 완화.

    양쪽 정렬은 줄 끝을 맞추려고 낱말 사이를 늘린다. 다음 어절이 조금 모자라 다음
    줄로 밀리면 그 줄 전체가 벌어지는데, 최소 공백 비율을 주면 한컴이 공백을 그만큼
    **압축해서** 어절을 그 줄에 끌어올 수 있어 벌어짐이 줄어든다.

    이 값이 실납품과 우리를 가른 진짜 차이였다(2026-08-24 실측): 실납품 알키미스트는
    본문 83%가 25%인데 우리는 0%였다 — 그래서 같은 양쪽 정렬인데 우리만 심하게
    벌어졌다(사용자 화면). 정렬 방식이 아니라 이 설정이 병인이다.
    """
    try:
        root = doc.headers[0].element
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] == "paraPr":
                node.set("condense", str(MIN_SPACE_RATIO_PERCENT))
        doc.headers[0].mark_dirty()
    except Exception:  # noqa: BLE001 — 표시 품질, 실패해도 문서는 유효
        pass


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
    if heading.bookmark:
        # 목차의 쪽번호 필드가 가리킬 표적. 값은 한컴이 채운다(hwpx_fields 참고).
        wrap_bookmark(para, heading.bookmark)
    return para


def _toc_page_ref(doc: HwpxDocument, para, text: str, bookmark: str) -> None:
    """목차 한 줄을 완성한다 — 제목 뒤 점선, 그 끝에 쪽번호 필드.

    쪽번호 몫을 **문단 오른쪽 여백**으로 떼어 두고 탭 자리를 그 앞에 세운다. 제목이 길어
    두 줄로 접혀도 점선과 번호는 마지막 줄 끝에 자리를 잡는다 — 여백이 없으면 제목이
    오른쪽 끝까지 차서 번호만 다음 줄로 밀린다.

    탭 위치는 쪽 왼쪽 여백 기준의 절대값이다(문단 들여쓰기와 무관). 실납품 보고서에서
    들여쓰기가 0·9.5·17mm인 세 수준이 같은 탭 정의 하나를 공유하는 것으로 확인했다.

    탭의 width는 한컴이 다시 계산하는 캐시 값이라 정확할 필요는 없지만, 갱신 전에도
    점선이 보이도록 남은 폭을 어림해 넣는다(글자 폭은 반각 기준 근사).
    """
    content_mm = PAGE_WIDTH_MM - MARGIN_MM["left"] - MARGIN_MM["right"]
    tab_mm = content_mm - TOC_NUMBER_RESERVE_MM
    doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(para),
        indent_right_mm=TOC_NUMBER_RESERVE_MM,
    )
    tab_pr_id = ensure_toc_tab_pr(doc, right_margin_mm=tab_mm)
    set_tab_pr(doc, para, tab_pr_id)
    # 반각 한 칸 ≈ 글자 크기의 절반. 목차는 본문 글자 크기를 쓴다.
    text_mm = _text_width(text) * (BODY_SIZE_PT / 2) * _MM_PER_PT
    remaining_mm = max(tab_mm - text_mm, 0.0)
    append_leader_tab(para, width_hwp=_mm_to_hwpunit(remaining_mm))
    append_page_ref(para, bookmark)


def _is_group_start(indent: int, prev_indent: int | None) -> bool:
    """ㅇ(1)이 하위 항목(- *, 2↑) 뒤에 와 새 논리 묶음을 여는지.

    □(0)은 여기서 다루지 않는다(_add_body가 indent==0을 늘 그룹 시작으로 처리).
    직전이 없거나(문단 시작·헤딩 직후) 같은/상위 수준이면 새 그룹이 아니다.
    """
    return indent == 1 and prev_indent is not None and prev_indent >= 2


def _add_body(
    doc: HwpxDocument,
    text: str,
    indent: int = 0,
    group_start: bool = False,
    align: str = "",
):
    char_id = doc.ensure_run_style(font=BODY_FONT, size=BODY_SIZE_PT)
    text = format_citations(_pad_marker(text))
    para = doc.add_paragraph(text, char_pr_id_ref=char_id, inherit_style=False)
    idx = doc.paragraphs.index(para)
    fmt: dict[str, float | int | str] = {
        "paragraph_index": idx,
        "line_spacing_percent": LINE_SPACING_PERCENT,
        # 문단마다 아래 여백을 줘 개조식 항목들이 붙어 보이지 않게 한다(가독성).
        "spacing_after_pt": BODY_SPACING_AFTER_PT,
    }
    fmt["alignment"] = align or BODY_ALIGNMENT
    # 마커 폭만큼 왼쪽 여백을 더 주고 첫 줄만 그만큼 당겨(음수) 내어쓰기를 만든다.
    # 이러면 항목이 두 줄 이상으로 넘어가도 둘째 줄이 마커 아래가 아니라 본문 글머리에
    # 맞춰 정렬돼, 마커 하나가 어디까지를 묶는지 눈으로 따라갈 수 있다(2026-08-11 지적).
    hanging = _hanging_indent_mm(text)
    if indent > 0 or hanging:
        # 개조식 계단 — 글머리 첫 줄이 (수준+1)×4pt에서 시작(□4·ㅇ8·-12).
        # 왼쪽 여백 = 첫 줄 위치 + 마커 폭, 첫 줄에서 마커 폭을 빼면(내어쓰기)
        # 줄바꿈된 둘째 줄이 본문 글머리에 정렬된다.
        fmt["indent_left_mm"] = (indent + 1) * OUTLINE_STEP_PT * _MM_PER_PT + hanging
    if hanging:
        fmt["first_line_indent_mm"] = -hanging
    if indent == 0 or group_start:
        # 대주제(□)·서술 문단, 또는 새 ㅇ 그룹 시작 앞에 여백을 줘 논리 묶음 사이를 벌린다.
        fmt["spacing_before_pt"] = GROUP_SPACING_BEFORE_PT
    doc.set_paragraph_format(**fmt)
    return para


def _is_outline_item(text: str) -> bool:
    """개조식 글머리로 시작하는 줄인가 — 마커 한 글자 + 공백."""
    return len(text) >= 2 and text[0] in OUTLINE_MARKERS and text[1] == " "


def _pad_marker(text: str) -> str:
    """좁은 마커('- ', '* ')를 전각 마커와 같은 칸 폭으로 채운다.

    칸 폭을 통일해야 계단이 성립한다 — 마커 폭이 제각각이면 본문 시작점이
    부모보다 왼쪽에 서는 역전이 생긴다(2026-08-24 실사고).
    """
    if not _is_outline_item(text):
        return text
    short = OUTLINE_SLOT_HALF - _text_width(text[:2])
    return text[:2] + " " * short + text[2:] if short > 0 else text


def _hanging_indent_mm(text: str) -> float:
    """개조식 마커 칸의 폭(mm). 마커로 시작하지 않으면 0.

    이 폭이 곧 내어쓰기 양이다 — 왼쪽 여백에 더하고 첫 줄에서 같은 값을 빼면 마커만
    왼쪽으로 튀어나오고 본문은 한 줄로 이어져 보인다. 마커 종류와 무관하게 같은 칸
    폭을 쓴다(_pad_marker가 좁은 마커를 그 폭까지 채운다).
    """
    if not _is_outline_item(text):
        return 0.0
    # _text_width는 반각을 1, 전각을 2로 세므로 반각 하나가 글자 크기의 절반에 해당한다.
    return OUTLINE_SLOT_HALF * (BODY_SIZE_PT * _MM_PER_PT / 2)


# 본문 인용 마커 — "(출처 13, 25)"·"(자료 3)". 렌더에서는 라벨을 걷고 번호만 남긴다:
# "…확대됨 (13, 25)" (2026-08-24 지시). 위첨자로 올렸다가 되돌렸다 — 번호 괄호가
# 참고문헌 목록과 바로 이어져 눈으로 따라가기 쉽다. 마커 앞에는 한 칸을 둔다.
# 라벨 집합·닫는 괄호 앞 공백은 core/citations 규약을 따른다(2026-09-03: 여기만
# "\s*" 없이 좁게 선언돼 "(출처 3 )"을 못 걷던 구멍 봉합. 세정이 라벨을 정본화하지만
# 렌더는 세정 밖 경로(수동 편집·구버전)도 받으므로 변형 라벨을 계속 수용한다).
_CITATION_RE = re.compile(r"[ \t]*\((?:출처|자료|근거|참고)\s*(\d+(?:\s*,\s*\d+)*)\s*\)")


_CITATION_SEP_RE = re.compile(r"\s*,\s*")


def format_citations(text: str) -> str:
    """본문 인용 마커에서 라벨을 걷고 번호만 남긴다 — "(출처 13, 25)" → " (13, 25)"."""

    def _numbers_only(match: re.Match[str]) -> str:
        return f" ({_CITATION_SEP_RE.sub(', ', match.group(1).strip())})"

    return _CITATION_RE.sub(_numbers_only, text)


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


def _fit_table_width(
    tbl, headers: list[str], rows: list[list[str]], weights: list[int] | None = None
) -> None:
    """표 전체 폭을 본문 폭에 맞추고 열 폭을 배분한다(페이지 넘침 방지).

    weights를 주면 그대로 쓰고, 없으면 내용 비례로 계산한다.
    """
    try:
        tbl.set_column_widths(weights or _column_weights(headers, rows))
    except Exception:  # noqa: BLE001 — 폭 배분 실패해도 표 자체는 유효
        pass


def _cell_para_id(doc: HwpxDocument) -> str | None:
    """표 셀에 붙일 가운데 정렬 문단 속성 id를 하나 만들어 재사용한다.

    셀 문단은 기본 paraPr(양쪽 정렬)을 쓴다. 한글 표에서 양쪽 정렬은 짧은 셀의
    글자 사이를 벌려 놓아 읽기 나쁘다(2026-08-10 지적). 왼쪽 정렬로 고정했다가
    가운데 정렬로 바꿨다(2026-08-20 지정) — 셀 세로 정렬은 이미 CENTER라 가로도
    가운데로 맞춰야 표 안 글자가 칸 한가운데에 놓인다.
    """
    if doc in _CELL_PARA_IDS:
        return _CELL_PARA_IDS[doc]
    para_id: str | None = None
    try:
        probe = doc.add_paragraph("")
        # 전 항목을 명시한다 — 프로브가 직전 문단 서식을 상속해 본문의 위 간격 9pt·
        # 들여쓰기가 셀 안까지 새어 들어왔다(2026-08-24 v6 실측: 셀 문단 prev=900).
        result = doc.set_paragraph_format(
            paragraph_index=doc.paragraphs.index(probe),
            alignment="CENTER",
            line_spacing_percent=CELL_LINE_SPACING_PERCENT,
            spacing_before_pt=CELL_PARA_SPACING_BEFORE_PT,
            spacing_after_pt=0.0,
            indent_left_mm=0.0,
            first_line_indent_mm=0.0,
        )
        para_id = str(result["paragraphs"][0]["paraPrIDRef"])
        doc.remove_paragraph(probe)
    except Exception:  # noqa: BLE001 — 정렬은 표시 품질, 실패해도 표는 유효
        para_id = None
    _CELL_PARA_IDS[doc] = para_id
    return para_id


def _align_cells_center(doc: HwpxDocument, tbl, n_rows: int, n_cols: int) -> None:
    para_id = _cell_para_id(doc)
    if para_id is None:
        return
    for r in range(n_rows):
        for c in range(n_cols):
            try:
                for para in tbl.cell(r, c).paragraphs:
                    para.para_pr_id_ref = para_id
            except Exception:  # noqa: BLE001 — 병합 셀 등 접근 실패는 건너뛴다
                continue


def _apply_cell_margins(tbl) -> None:
    """셀 안 여백을 한컴 기본값으로 되돌린다 — 글자가 괘선에 붙지 않게.

    python-hwpx가 만든 표는 표의 `inMargin`(모든 셀의 기본 안 여백)과 셀마다의
    `cellMargin`이 모두 0이다. 한컴이 실제로 쓰는 값은 좌우 510·상하 141 HWPUNIT이며,
    `hasMargin`은 0(=기본값을 쓴다)인 채로 둔다 — 실납품 샘플과 같은 모양이다.
    두 자리를 같은 값으로 채우므로 한컴이 어느 쪽을 읽든 결과가 같다.
    """
    element = getattr(tbl, "element", None)
    if element is None:
        return
    attrs = {
        "left": str(CELL_MARGIN_SIDE_HWP),
        "right": str(CELL_MARGIN_SIDE_HWP),
        "top": str(CELL_MARGIN_VERT_HWP),
        "bottom": str(CELL_MARGIN_VERT_HWP),
    }
    for node in element.iter():
        # outMargin(표 바깥 여백)은 건드리지 않는다 — 본문과의 간격이라 성격이 다르다.
        if node.tag.rsplit("}", 1)[-1] in {"inMargin", "cellMargin"}:
            node.attrib.update(attrs)
    try:
        tbl.mark_dirty()
    except Exception:  # noqa: BLE001 — 여백은 표시 품질, 실패해도 표는 유효
        pass


def _add_table_caption(doc: HwpxDocument, caption: str, bookmark: str = "") -> None:
    """표 제목 한 줄 — 표 바로 위, 가운데·굵게, 표와 같은 쪽에 묶는다(2026-08-24 지시)."""
    char_id = doc.ensure_run_style(font=HEADING_FONT, size=TABLE_CAPTION_SIZE_PT, bold=True)
    para = doc.add_paragraph(caption, char_pr_id_ref=char_id, inherit_style=False)
    result = doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(para),
        alignment="CENTER",
        line_spacing_percent=LINE_SPACING_PERCENT,
        spacing_before_pt=GROUP_SPACING_BEFORE_PT,
        spacing_after_pt=TABLE_CAPTION_SPACE_AFTER_PT,
    )
    _keep_with_next(doc, result)
    if bookmark:
        wrap_bookmark(para, bookmark)  # 표 목차 줄의 쪽번호가 가리킬 표적


def _keep_with_next(doc: HwpxDocument, fmt_result: Any) -> None:
    """문단의 paraPr에 '다음 문단과 함께'를 심는다 — 제목만 앞 쪽에 남지 않게.

    캡션 서식 조합의 paraPr는 캡션끼리만 공유하므로 헤더 정의를 바꿔도 안전하다.
    """
    try:
        pr_id = str(fmt_result["paragraphs"][0]["paraPrIDRef"])
        root = doc.headers[0].element
        for pr in root.iter():
            if pr.tag.rsplit("}", 1)[-1] == "paraPr" and pr.get("id") == pr_id:
                for node in pr.iter():
                    if node.tag.rsplit("}", 1)[-1] == "breakSetting":
                        node.set("keepWithNext", "1")
                break
    except Exception:  # noqa: BLE001 — 쪽 묶음은 표시 품질, 실패해도 문서는 유효
        pass


def _add_table_note(
    doc: HwpxDocument, text: str, *, align: str, after_pt: float, indent_mm: float = 0.0
) -> None:
    """표 부속 한 줄 — 단위(표 위, 우측 정렬)·출처(표 아래, 좌측 정렬)에 쓴다."""
    char_id = doc.ensure_run_style(font=HEADING_FONT, size=TABLE_NOTE_SIZE_PT)
    para = doc.add_paragraph(text, char_pr_id_ref=char_id, inherit_style=False)
    fmt: dict[str, float | int | str] = {
        "paragraph_index": doc.paragraphs.index(para),
        "alignment": align,
        "line_spacing_percent": LINE_SPACING_PERCENT,
        "spacing_after_pt": after_pt,
    }
    if indent_mm:
        fmt["indent_left_mm"] = indent_mm
    doc.set_paragraph_format(**fmt)


def _reset_table_anchor(doc: HwpxDocument, tbl) -> None:
    """표·그림 박스의 앵커 문단 들여쓰기를 0으로 되돌리고 가운데 정렬한다.

    앵커 문단이 직전 개조식 문단의 서식을 상속해, 박스 전체가 글머리 들여쓰기만큼
    오른쪽으로 밀려 렌더됐다(2026-08-21 지적). 블록은 본문 위계와 독립이다.
    가운데 정렬은 "표는 좌우에서 항상 가운데"(2026-08-24 지시) — 본문 폭보다 좁게
    잡힌 표도 항상 페이지 한가운데에 놓인다.
    """
    try:
        idx = doc.paragraphs.index(tbl.paragraph)
        doc.set_paragraph_format(
            paragraph_index=idx,
            indent_left_mm=0.0,
            first_line_indent_mm=0.0,
            alignment="CENTER",
        )
    except Exception:  # noqa: BLE001 — 서식 리셋 실패해도 표는 유효
        pass


def _add_block_gap(doc: HwpxDocument) -> None:
    """표·그림 블록 뒤의 빈 줄 — 다음 문장이 블록에 붙지 않게(2026-08-24 지시)."""
    char_id = doc.ensure_run_style(font=BODY_FONT, size=BODY_SIZE_PT)
    para = doc.add_paragraph("", char_pr_id_ref=char_id, inherit_style=False)
    doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(para),
        line_spacing_percent=100,
        indent_left_mm=0.0,
        first_line_indent_mm=0.0,
    )


def _enable_header_repeat(tbl) -> None:
    """쪽을 걸치는 표에서 머리행을 반복한다 — 공공 보고서 관례.

    라이브러리가 repeatHeader=0으로 하드코딩해 두어 XML 속성을 직접 바꾼다.
    """
    try:
        tbl.element.set("repeatHeader", "1")
        tbl.mark_dirty()
    except Exception:  # noqa: BLE001 — 반복 실패해도 표는 유효
        pass


def _add_table(doc: HwpxDocument, table: Table) -> None:
    if table.caption:
        _add_table_caption(doc, table.caption, table.caption_bookmark)
    if table.unit:
        # 단위 줄은 실측 관례대로 캡션과 표 사이 우측 정렬로 붙인다.
        _add_table_note(doc, table.unit, align="RIGHT", after_pt=TABLE_CAPTION_SPACE_AFTER_PT)
    n_rows = len(table.rows) + 1
    n_cols = len(table.headers)
    # width=본문 폭: 표가 페이지를 넘지 않게 하고, 긴 셀은 열 폭 안에서 줄바꿈된다.
    tbl = doc.add_table(n_rows, n_cols, width=_page_content_width_hwp())
    _reset_table_anchor(doc, tbl)
    _enable_header_repeat(tbl)
    for col, head in enumerate(table.headers):
        # 음영 없음 — 배경색은 전 요소에서 쓰지 않는다(2026-08-23 사용자 지시).
        # 머리행 구분은 굵기·괘선이 맡는다.
        tbl.set_cell_text(0, col, head)
    for row_idx, row in enumerate(table.rows, start=1):
        # 비정형 행 방어 - 모델이 3열 표에 4칸 행을 쓰는 실사례(2026-08-21 v6:
        # IndexError로 렌더 전체가 죽었다). 초과 셀은 버리고 로그로 남긴다.
        if len(row) > n_cols:
            logger.warning(
                "hwpx.table.ragged_row",
                caption=(table.caption or "")[:40],
                row=row_idx,
                n_cells=len(row),
                n_cols=n_cols,
            )
        for col, value in enumerate(row[:n_cols]):
            tbl.set_cell_text(row_idx, col, value)
    _fit_table_width(tbl, table.headers, table.rows, table.column_weights)
    _align_cells_center(doc, tbl, n_rows, n_cols)
    _apply_cell_margins(tbl)
    if table.source:
        # 출처는 표 바로 아래 — ※ 시작·들여쓰기(2026-08-24 지시, 실납품 "※ 출처 :" 관례).
        _add_table_note(
            doc,
            table.source,
            align="LEFT",
            after_pt=BODY_SPACING_AFTER_PT,
            indent_mm=SOURCE_NOTE_INDENT_MM,
        )
    _add_block_gap(doc)


def _add_caption_below(doc: HwpxDocument, caption: str, bookmark: str = "") -> None:
    """그림 제목 — 그림 **아래**, 굵게·가운데(2026-08-24 지시: 표는 위, 그림은 아래)."""
    char_id = doc.ensure_run_style(font=HEADING_FONT, size=TABLE_CAPTION_SIZE_PT, bold=True)
    para = doc.add_paragraph(caption, char_pr_id_ref=char_id, inherit_style=False)
    doc.set_paragraph_format(
        paragraph_index=doc.paragraphs.index(para),
        alignment="CENTER",
        line_spacing_percent=LINE_SPACING_PERCENT,
        spacing_before_pt=TABLE_CAPTION_SPACE_AFTER_PT,
        spacing_after_pt=BODY_SPACING_AFTER_PT,
    )
    if bookmark:
        wrap_bookmark(para, bookmark)  # 그림 목차 줄이 가리킬 표적


def _add_chart(doc: HwpxDocument, chart: Chart) -> None:
    """차트 — 스펙을 PNG로 그려 가운데 넣고, 제목을 그림 아래에 단다.

    못 그리면 보관해 둔 원본 표로 되돌린다. 여기서 예외를 그냥 올리면 그림 하나 때문에
    보고서 전체가 안 나온다 — 운영 컨테이너에 한글 폰트가 없기만 해도 그렇게 됐다
    (2026-08-28 실측). 번호는 그림 번호 그대로 둔다: 그림 목차 줄이 이 자리를 가리키고
    있어, 번호를 바꾸면 목차가 없는 곳을 가리킨다.
    """
    from src.export.chart_render import ChartRenderError, render_png  # 지연 import

    try:
        png = render_png(chart.spec)
    except ChartRenderError as exc:
        logger.warning("hwpx.chart_render_failed", caption=chart.caption, detail=str(exc))
        if chart.fallback is None:
            return  # 되돌릴 표조차 없으면 건너뛴다 — 깨진 그림보다 없는 편이 낫다
        _add_table(
            doc,
            replace(
                chart.fallback,
                caption=chart.caption,
                caption_bookmark=chart.caption_bookmark,
            ),
        )
        return
    doc.add_picture(
        png,
        "png",
        width_mm=PAGE_WIDTH_MM - MARGIN_MM["left"] - MARGIN_MM["right"],
        height_mm=CHART_HEIGHT_MM,
        align="CENTER",
    )
    try:
        # 그림 앵커 문단이 직전 개조식 서식을 상속하지 않게 리셋(표 앵커와 같은 이유).
        doc.set_paragraph_format(
            paragraph_index=len(doc.paragraphs) - 1,
            indent_left_mm=0.0,
            first_line_indent_mm=0.0,
            alignment="CENTER",
        )
    except Exception:  # noqa: BLE001 — 서식 리셋 실패해도 그림은 유효
        pass
    _add_caption_below(doc, chart.caption, chart.caption_bookmark)
    _add_block_gap(doc)


def _add_figure(doc: HwpxDocument, figure: Figure) -> None:
    """그림 플레이스홀더 — 추천 시각자료 설명 박스 + 그 아래 제목.

    박스 높이를 실제 그림만 하게 잡는다(2026-08-24 지시). 글자 높이에 맞춘 납작한
    박스는 자리를 제대로 잡아 주지 못해, 나중에 진짜 그림을 끼우면 뒷 페이지가
    통째로 밀린다 — 자리표시자는 '그 자리가 얼마나 필요한지'를 보여야 뜻이 산다.
    """
    note = "※ 실제 이미지는 확정 후 삽입되는 자리표시자입니다."
    if figure.source_hints:
        # 원본을 찾아갈 자리 — 링크가 있으면 그대로 싣는다(한컴에서 눌러 열 수 있다).
        note += " 원본 참고: " + " · ".join(figure.source_hints[:_FIGURE_HINT_MAX])
    tbl = doc.add_table(2, 1, width=_page_content_width_hwp())
    _reset_table_anchor(doc, tbl)
    tbl.set_cell_text(0, 0, f"추천 시각자료: {figure.description}")
    tbl.set_cell_text(1, 0, note)
    # 배경색은 전 요소에서 쓰지 않는다(2026-08-23 사용자 지시).
    # 셀 정렬은 손대지 않는다 — 설명 문장이 길어 가운데로 모으면 줄마다 들쭉날쭉해진다.
    _apply_cell_margins(tbl)
    _set_box_height(tbl, FIGURE_BOX_HEIGHT_MM, FIGURE_NOTE_ROW_MM)
    _add_caption_below(doc, figure.caption, figure.caption_bookmark)
    _add_block_gap(doc)


def _set_box_height(tbl, total_mm: float, last_row_mm: float) -> None:
    """표 박스의 세로 크기를 지정한다 — 마지막 행만 낮게, 나머지를 첫 행이 갖는다."""
    element = getattr(tbl, "element", None)
    if element is None:
        return
    total = _mm_to_hwpunit(total_mm)
    last = _mm_to_hwpunit(last_row_mm)
    try:
        rows = [n for n in element.iter() if n.tag.rsplit("}", 1)[-1] == "tr"]
        if not rows:
            return
        for i, row in enumerate(rows):
            height = last if i == len(rows) - 1 else (total - last) // max(1, len(rows) - 1)
            for cell_sz in row.iter():
                if cell_sz.tag.rsplit("}", 1)[-1] == "cellSz":
                    cell_sz.set("height", str(height))
        for node in element.iter():
            if node.tag.rsplit("}", 1)[-1] == "sz" and node.get("height"):
                node.set("height", str(total))
                break
        tbl.mark_dirty()
    except Exception:  # noqa: BLE001 — 크기는 표시 품질, 실패해도 박스는 유효
        pass
