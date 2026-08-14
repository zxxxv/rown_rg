"""조립된 보고서 → HWPX 파일 — 파이프라인과 hwpx_writer 사이의 변환 계층.

선택된 섹션 초안(LLM이 쓴 마크다운)을 hwpx_writer의 Block 시퀀스로 바꿔 렌더한다.
실제 보고서 꼴을 갖추도록 **표지 → 요약문 → 목차 → 표·그림 목차 → 본문(장별)** 순으로
조립한다(실납품 보고서 6종 실측 관례, 2026-08-11).

작성 규칙(src/prompts) 반영:
- 첫 장은 표지(제목·기관·날짜), 둘째 장은 목차다.
- 본문은 개조식(□ 대주제 → ㅇ → - → *)으로, 마커 수준마다 들여쓰기해 렌더한다.
- 약어는 "풀네임(약어)" 형식으로 첫 등장하므로(agent_writing_style.md), 장 단위로 수집해
  각 장 끝에 "약어 정리"표로 정리한다.
- 표가 없는 절에는 추천 시각자료(그림) 자리표시자를 넣는다(2페이지당 시각자료 1개 규칙).
- 표 셀의 인라인 마크다운(**강조**·링크)은 벗기고, 표 폭은 본문 폭에 맞춰 긴 셀이 줄바꿈된다.
- 참고 표기 (출처 n)은 본문에서 **걷어낸다**(2026-08-11). 참고해 재작성한 문장에 출처
  번호가 덕지덕지 붙으면 독자가 본문을 원문에서 검색했다 못 찾고 표기를 불신한다.
  출처는 참고문헌 최종장으로만 밝힌다. 직접 인용 [n]은 원문을 그대로 옮긴 문장이라
  그대로 남긴다. (웹 프리뷰는 참고 표기를 블록 우상단에 모아 보여준다.)
  예외: 표 바로 아래 (출처 n) 단독 줄은 걷어내지 않고 표 하단 실서지 줄로 변환한다 —
  캡션 위·출처 아래가 실측한 공공 보고서 관례다.
- 표에는 제목을 붙인다: 작성 규칙이 표 위에 "표: 제목" 한 줄을 쓰게 하고, 번호(<표 N-M>)는
  장 단위 일련번호로 이 계층에서 매긴다 — 절이 나뉘어 작성돼 모델은 번호를 알 수 없다.
  제목과 표 사이 "(단위: …)" 단독 줄은 표의 단위 줄로 흡수한다.
- 번호는 텍스트에 직접 넣는다("제1장", "1.1 …"). 헤딩에 개요 자동번호를 부여하지 않아
  한컴이 제목·목차 앞에 "1.", "2." 같은 개요 번호를 붙이지 않는다.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, timedelta, timezone
from pathlib import Path
from uuid import UUID

import structlog

from src.core.charts import CHART_FENCE_RE, ChartSpec, ChartSpecError, parse_chart_spec
from src.core.citations import strip_source_marks
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import SectionPlan, SourceRef, SourceType
from src.export.hwpx_writer import (
    HEADER_TEXT,
    Block,
    Chart,
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

# 렌더 규칙을 바꿀 때마다 올린다. 다운로드는 "본문이 파일보다 새것인가"로만 재렌더를
# 판단하는데, 코드를 고쳐도 본문 수정 시각은 그대로라 옛 파일이 그대로 내려갔다
# (2026-08-11 지적). 버전을 파일명에 섞어 새 코드가 옛 산출물을 집지 않게 한다.
EXPORT_RENDER_VERSION = 8  # r8: 표 출처 줄 라벨 변형("* 출처: (출처 n)") 실서지 변환(2026-08-15)


def export_filename(project_id: UUID | str) -> str:
    """산출물 파일명 — 렌더 버전이 들어가 코드가 바뀌면 자연히 새 파일이 된다."""
    return f"{project_id}.r{EXPORT_RENDER_VERSION}.hwpx"


def export_file_pattern(project_id: UUID | str) -> str:
    """그 프로젝트가 남긴 모든 산출물 glob — 옛 버전과 버전 없던 파일까지 포함(정리용)."""
    return f"{project_id}*.hwpx"


logger = structlog.get_logger(__name__)

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
    """참고 표기 (출처 n) 제거. 직접 인용 [n]은 남긴다 — 원문을 옮긴 문장이라 출처가 필요하다."""
    return strip_source_marks(text)


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


# 연도 어깨점 정규화 — 실납품 샘플의 고질병(‘24 / `24 / '24 혼용, 2026-08-11 실측)을
# 오른쪽 홑따옴표(U+2019) 하나로 통일한다. ’24년·’24.5.·’23~’27 꼴만 건드린다.
_YEAR_QUOTE_RE = re.compile(r"[‘'`´](?=\d{2}(?:년|[.~∼]))")


def _clean_inline(text: str) -> str:
    """HWPX 평문 렌더에서 의미 없는 인라인 기호 제거(강조·링크·발명 인용 마커)."""
    text = text.replace("**", "")
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _YEAR_QUOTE_RE.sub("’", text)
    return _strip_invented_markers(text)


# 표 제목 줄 — 표 바로 위에 오는 "표: 제목"(작성 규칙) 및 모델이 번호까지 쓴 변형을 잡는다.
# 번호 뒤 구분자는 콜론·대괄호·꺾쇠로 한정한다: 마침표까지 허용하면 "표 3.1에서 보듯이"처럼
# 표를 가리키는 평범한 서술 문장이 제목으로 오인된다.
_TABLE_CAPTION_RE = re.compile(
    r"^(?:[□ㅇ○◦\-*]\s+)?"  # 개조식 마커를 달고 쓴 경우까지 허용
    r"(?:\[\s*표[^\]]*\]|<\s*표[^>]*>|표\s*[\d\-]*\s*[:：])"  # "[표 2-1]"·"<표 2-1>"·"표:"
    r"\s*(?P<title>.+)$"
)

# 단위 줄 — 표 제목과 표 사이 "(단위: 백만 원)" 단독 줄(작성 규칙의 표 3종 세트).
_TABLE_UNIT_RE = re.compile(r"^[（(]\s*단위\s*[:：][^)）]*[)）]$")
# 제목 꼬리에 붙은 단위 — 빈 줄 없이 이어 쓰면 파서가 제목·단위를 한 문단으로 합친다.
_CAPTION_TRAILING_UNIT_RE = re.compile(r"\s*(?P<unit>[（(]\s*단위\s*[:：][^)）]*[)）])$")


def _pop_table_caption(blocks: list[Block]) -> tuple[str, str]:
    """직전 블록들에서 표 제목·단위 줄을 떼어내 (제목, 단위)로 반환한다(없으면 빈 문자열).

    제목을 문단으로 남겨 두면 표와 분리돼 본문처럼 떠 버리므로 Table 필드로 옮긴다.
    모델이 쓴 번호는 버린다 — 절 단위로 나눠 작성되는 탓에 장 전체에서 중복·누락되기
    때문이다. 최종 번호는 _number_visuals가 장 단위로 다시 매긴다.
    """
    unit = ""
    if blocks:
        last = blocks[-1]
        if isinstance(last, Paragraph) and _TABLE_UNIT_RE.match(last.text.strip()):
            unit = last.text.strip()
            blocks.pop()
    if not blocks:
        return "", unit
    last = blocks[-1]
    if not isinstance(last, Paragraph):
        return "", unit
    match = _TABLE_CAPTION_RE.match(last.text.strip())
    if not match:
        return "", unit
    blocks.pop()
    title = match.group("title").strip()
    trailing = _CAPTION_TRAILING_UNIT_RE.search(title)
    if trailing and not unit:
        unit = trailing.group("unit")
        title = title[: trailing.start()].strip()
    return title, unit


# 표 머리행에서 제목을 세울 때 분류축으로 못 쓰는 일반어 — "구분별 값"처럼 뜻 없는 제목이 된다.
_GENERIC_HEADERS: frozenset[str] = frozenset(
    {"구분", "항목", "분류", "내용", "비고", "번호", "순번", "값", "단계", "특징"}
)
# 제목에 실을 열 수 — 다 나열하면 제목이 표만큼 길어진다.
_CAPTION_MAX_COLS = 3


def _caption_from_headers(headers: list[str], section_title: str) -> str:
    """표 머리행으로 제목을 세운다 — 모델이 제목을 안 쓴 옛 본문의 폴백.

    절 제목을 그대로 쓰면 한 절의 표 네 개가 전부 같은 이름이 된다(2026-08-11 지적).
    머리행은 그 표가 무엇을 담았는지 이미 말하고 있으므로, 첫 열을 분류축 삼아
    "국가별 노형·상용화 시점"처럼 세운다. 첫 열이 '구분'같은 일반어면 축이 못 되니
    절 제목을 앞에 두고 나머지 열을 잇는다.
    """
    cols = [h.strip() for h in headers if h.strip()]
    if len(cols) < 2:
        return section_title
    if cols[0] in _GENERIC_HEADERS:
        rest = [c for c in cols[1:] if c not in _GENERIC_HEADERS][: _CAPTION_MAX_COLS - 1]
        return f"{section_title}: {'·'.join(rest)}" if rest else section_title
    return f"{cols[0]}별 {'·'.join(cols[1:_CAPTION_MAX_COLS])}"


def _number_visuals(
    blocks: list[Block],
    chapter: int,
    table_no: int,
    figure_no: int,
    fallback_title: str,
) -> tuple[int, int]:
    """표·그림에 "<표 N-M> 제목"·"<그림 N-M> 제목" 캡션을 매기고 다음 번호들을 반환한다.

    꺾쇠 표기는 실납품 보고서 실측 다수 관행이다(2026-08-11, 6종 중 4종). M은 장 안에서
    이어지는 번호다(표와 그림이 각각 따로 센다). 절 단위로 나눠 작성되는 탓에 모델은
    이 번호를 알 수 없어 조립 단계에서만 매길 수 있다. 모델이 제목을 빠뜨린 표는
    절 제목으로 채워 번호 없는 표가 남지 않게 한다.
    """
    for i, block in enumerate(blocks):
        if isinstance(block, Table):
            table_no += 1
            title = block.caption or _caption_from_headers(block.headers, fallback_title)
            blocks[i] = replace(
                block,
                caption=f"<표 {chapter}-{table_no}> {title}",
                caption_bookmark=f"rown_t{chapter}_{table_no}",
            )
        elif isinstance(block, Figure):
            figure_no += 1
            blocks[i] = replace(
                block,
                caption=f"<그림 {chapter}-{figure_no}> {block.caption}",
                caption_bookmark=f"rown_f{chapter}_{figure_no}",
            )
        elif isinstance(block, Chart):
            # 차트도 그림이다 — 자리표시자와 같은 번호 흐름을 쓴다.
            figure_no += 1
            title = getattr(block.spec, "title", "") or fallback_title
            blocks[i] = replace(
                block,
                caption=f"<그림 {chapter}-{figure_no}> {title}",
                caption_bookmark=f"rown_f{chapter}_{figure_no}",
            )
    return table_no, figure_no


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


# 차트 펜스를 잠시 대신할 표식 — 파서가 펜스 본문을 문단으로 흘리지 않게 한 줄로 줄인다.
_CHART_TOKEN = "\x00chart:{index}\x00"
_CHART_TOKEN_RE = re.compile(r"^\x00chart:(\d+)\x00$")


def _extract_charts(md: str) -> tuple[str, list[ChartSpec]]:
    """차트 펜스를 한 줄 표식으로 바꾸고 스펙 목록을 함께 돌려준다.

    그릴 수 없는 스펙(값 개수 불일치·숫자 아님 등)은 차트를 포기하고 **원본 표**로
    되돌린다 — 보관해 둔 spec.table이 그 안전망이다. 원본 표도 없으면 통째로 버린다.
    틀린 그래프보다 표가, 표보다 아무것도 없는 게 낫다.
    """
    specs: list[ChartSpec] = []

    def _sub(match: re.Match[str]) -> str:
        try:
            specs.append(parse_chart_spec(match.group("body")))
        except ChartSpecError as exc:
            logger.warning("export.chart_spec_invalid", detail=str(exc))
            fallback = _CHART_TABLE_RE.search(match.group("body"))
            return textwrap.dedent(fallback.group("table")) if fallback else ""
        return _CHART_TOKEN.format(index=len(specs) - 1)

    return CHART_FENCE_RE.sub(_sub, md), specs


# 못 그리는 차트에서 원본 표를 건져 내기 위한 최소 추출 — 'table: |' 뒤 들여쓴 블록.
_CHART_TABLE_RE = re.compile(r"^table:[ \t]*\|[ \t]*\n(?P<table>(?:[ \t]+.*\n?)+)", re.M)


def markdown_to_blocks(md: str) -> list[Block]:
    """마크다운 본문을 Heading/Paragraph/Table 블록으로 변환.

    지원 범위는 LLM 산출물에서 실제로 나오는 형태(헤딩·개조식 문단·GFM 표)로 한정한
    보수적 파서다. 개조식 마커(□ ㅇ - *)는 마커를 보존한 채 수준별로 들여쓰기하며,
    각 마커 줄은 독립 문단이 된다. 인식 못 하는 줄은 문단으로 강등된다(내용 유실 없음).
    """
    blocks: list[Block] = []
    para_buf: list[str] = []
    table_buf: list[list[str]] = []
    md, charts = _extract_charts(md)

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
            # 표 제목·단위 줄은 표 위 문단으로 들어오므로 여기서 흡수한다(번호는 조립 단계에서).
            caption, unit = _pop_table_caption(blocks)
            blocks.append(Table(headers=cleaned[0], rows=cleaned[1:], caption=caption, unit=unit))

    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            flush_para()
            flush_table()
            continue
        token = _CHART_TOKEN_RE.match(line)
        if token:
            flush_para()
            flush_table()
            # 캡션은 조립 단계에서 그림 번호와 함께 채운다(_number_visuals).
            blocks.append(Chart(spec=charts[int(token.group(1))], caption=""))
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


# 표 아래 출처 줄 — "(출처 3)"/"(출처 3, 7)" 단독. 앞의 불릿(※·*·-)과 "출처:"/"자료:"
# 라벨도 허용한다 — 작성기가 규약대로 단독 마커만 내지 않고 "* 출처: (출처 34, 21)"처럼
# 라벨을 붙여 내는 게 실제 출력이었다(2026-08-15 실측: 라벨 변형이 안 잡혀 실서지 변환이
# 통째로 빠지고, 마커만 걷힌 빈 '출처:' 껍데기가 표 밑에 남았다). 조립 시 실서지로 바꾼다.
_TABLE_SOURCE_RE = re.compile(
    r"^(?:[※*\-–ㅇ○◦]\s*)?(?:(?:출처|자료|참고)\s*[:：]\s*)?"
    r"[（(]\s*출처\s*(?P<nums>\d{1,3}(?:\s*,\s*\d{1,3})*)\s*[)）]$"
)


def _attach_table_sources(blocks: list[Block], titles: dict[int, str]) -> None:
    """표 바로 다음의 (출처 n) 단독 문단을 떼어 표 하단 실서지 줄로 옮긴다.

    본문의 (출처 n)은 걷어내는 규칙이지만, 표 출처만은 실측 관례(캡션 위·출처 아래)대로
    참고문헌 번호가 가리키는 자료 제목을 풀어 표 아래 남긴다.
    """
    i = 0
    while i < len(blocks) - 1:
        block, nxt = blocks[i], blocks[i + 1]
        if isinstance(block, Table) and isinstance(nxt, Paragraph):
            match = _TABLE_SOURCE_RE.match(nxt.text.strip())
            if match:
                nums = [int(n) for n in match.group("nums").split(",")]
                names = [titles.get(n, f"자료 {n}") for n in nums]
                blocks[i] = replace(block, source="출처: " + "; ".join(names))
                del blocks[i + 1]
        i += 1


# "(출처 n)"을 걷어내면 "- 출처:"처럼 라벨만 남는 줄이 생긴다 — 가리키는 것이 없어진
# 껍데기라 통째로 버린다(2026-08-12 지적: 표 밑에 빈 '출처:'만 떠 있었다).
_BARE_SOURCE_LABEL_RE = re.compile(r"^[□ㅇ○◦\-*※\s]*(?:출처|자료|참고)\s*[:：]?\s*$")


def _strip_citation_blocks(blocks: list[Block]) -> list[Block]:
    """블록들에서 참고 표기 (출처 n)을 걷어낸다 — 문단·표 제목·셀 전부.

    출처 표기만 담고 있던 문단은 빈 껍데기가 되므로 통째로 버린다. 파싱 전에 원문에서
    걷어내지 않는 이유: 표 하단 출처 줄(_attach_table_sources)이 먼저 살아남아야 한다.
    """
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, Paragraph):
            text = _strip_citations(block.text).strip()
            if not text or _BARE_SOURCE_LABEL_RE.match(text):
                continue
            out.append(replace(block, text=text))
        elif isinstance(block, Table):
            out.append(
                replace(
                    block,
                    caption=_strip_citations(block.caption).strip(),
                    headers=[_strip_citations(h) for h in block.headers],
                    rows=[[_strip_citations(c) for c in row] for row in block.rows],
                )
            )
        else:
            out.append(block)
    return out


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


# 약어 정리표 배치 — 한 단(段)에 15줄, 두 단을 나란히(=한 표에 30개). 넘치면 다음 표로.
# 3열 한 줄짜리는 본문 폭을 다 쓰면서 내용이 몇 글자뿐이라 페이지가 비어 보였다
# (2026-08-10 지적). 두 단으로 접어 폭을 절반씩 쓴다.
# 한 표에 담을 행 수(단 하나 기준) — 설명이 두세 줄로 접히는 행을 감안해 한 쪽에 들어갈
# 만큼만 잡는다. 넘치면 표가 쪽을 걸쳐 잘리고 제목만 남은 빈 쪽이 생긴다.
_GLOSSARY_ROWS_PER_COLUMN = 10
_GLOSSARY_HEADERS = ["약어", "전체 명칭", "설명", "약어", "전체 명칭", "설명"]
# 약어 표 열 폭은 내용이 아니라 **고정 비율**로 준다. 표마다 내용으로 계산하면 쪽을
# 넘길 때마다 열 폭이 달라져 같은 표가 여러 모양으로 보인다(2026-08-12 지적).
_GLOSSARY_WEIGHTS = [14, 20, 16, 14, 20, 16]


def _glossary_tables(entries: list[list[str]]) -> list[Table]:
    """약어 목록 → 2단 표들. 한 표가 한 쪽에 들어가도록 행 수를 묶는다.

    행 수를 줄인 이유가 있다. 표는 남은 자리에 안 들어가면 통째로 다음 쪽으로 밀리는데,
    그러면 "약어 정리" 제목만 남은 빈 쪽이 생기고 표는 쪽을 걸쳐 잘린다(2026-08-12 지적).
    설명이 길어 두세 줄로 접히는 행이 섞이므로 여유를 두고 잡는다.

    마지막 표는 남은 항목을 좌우로 **반씩** 나눈다 — 왼쪽부터 채우면 오른쪽 절반이
    통째로 빈 표가 나온다.
    """
    per_table = _GLOSSARY_ROWS_PER_COLUMN * 2
    tables: list[Table] = []
    for start in range(0, len(entries), per_table):
        chunk = entries[start : start + per_table]
        half = (len(chunk) + 1) // 2
        left, right = chunk[:half], chunk[half:]
        rows = [left[i] + (right[i] if i < len(right) else ["", "", ""]) for i in range(len(left))]
        tables.append(Table(headers=_GLOSSARY_HEADERS, rows=rows, column_weights=_GLOSSARY_WEIGHTS))
    return tables


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
    """표지 부제 — 프로젝트가 명시한 값만 쓴다.

    한때 주제문(topic)을 부제로 올렸지만, topic은 "무엇을 어떤 범위로 검토하라"는
    **검색·작성 지시문**이지 표지에 실을 문장이 아니다(2026-08-10 판단). 지시문을
    납품물 표지에 인쇄하면 읽는 사람에게는 군더더기다. 부제를 따로 받기 전까지는
    표지에서 생략한다.
    """
    options = state.options if isinstance(state.options, dict) else {}
    return str(options.get("subtitle") or "").strip()


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


# 목차 줄과 본문 제목을 잇는 책갈피 이름. 한컴 상호참조가 이름으로 표적을 찾으므로
# 문서 안에서 유일하고 안정적이어야 한다 — 제목 글자가 아니라 번호로 짓는다(제목이
# 바뀌거나 같은 제목이 두 번 나와도 안 흔들린다).
_REFERENCES_BOOKMARK = "rown_ref"


def _toc_bookmark(chapter_number: int, section_number: int | None = None) -> str:
    if section_number is None:
        return f"rown_ch{chapter_number}"
    return f"rown_s{chapter_number}_{section_number}"


def _chapter_heading_text(chapter_number: int, ch_titles: dict[int, str]) -> str:
    """장 헤딩 텍스트 — '제N장  제목'(제목 없으면 '제N장')."""
    ch_title = ch_titles.get(chapter_number)
    return f"제{chapter_number}장  {ch_title}" if ch_title else f"제{chapter_number}장"


# A4 2페이지 ≈ 1,500자 — 이 분량마다 시각자료 1개가 작성 규칙(agent_visual_rules.md)이다.
_CHARS_PER_VISUAL = 1500


def _figures_needed(content: str, visual_count: int, key_points: Sequence[str] = ()) -> int:
    """그 절에 더 넣을 그림 수 — 분량이 요구하는 시각자료 수에서 이미 있는 것을 뺀 만큼.

    visual_count는 표와 차트를 함께 센다 — 둘 다 시각자료라, 차트를 안 세면 이미 그림이
    있는 절에 자리표시자가 덧붙는다.

    한때는 "표가 하나도 없는 절"에만 그림을 넣었다. 그러다 보니 표를 많이 쓴 절일수록
    그림이 사라져, 표만 빽빽한 보고서가 됐다(2026-08-11 지적). 분량 기준으로 바꿔
    표가 있어도 긴 절에는 그림이 함께 들어가게 한다.

    자리표시자 수는 key_points 수로 캡한다(없으면 1) — 설명을 하나씩 나눠 맡을 소재가
    떨어지면 남는 그림이 같은 제목·같은 폴백 설명으로 복제된다(2026-08-13 실사고:
    key_points 없는 절에 동일 그림 4개가 나란히 배치됨).
    """
    required = max(1, len(content) // _CHARS_PER_VISUAL)
    needed = max(0, required - visual_count)
    return min(needed, max(1, len(key_points)))


def _figure_placeholder(plan: SectionPlan, index: int = 0) -> Figure:
    """추천 시각자료(그림) 자리표시자. index는 한 절에서 몇 번째 그림인지.

    실제 이미지를 못 구하므로, 절의 초점(key_points→direction→제목)을 근거로 어떤
    그림이 적합한지 설명을 적어 배치한다. 한 절에 여러 개면 key_points를 하나씩 나눠
    맡겨 같은 설명이 반복되지 않게 한다. 캡션 번호는 조립 단계에서 장 단위로 매긴다.
    """
    if index < len(plan.key_points):
        focus = plan.key_points[index]
    elif plan.key_points:
        focus = plan.key_points[0]
    elif plan.direction:
        focus = plan.direction
    else:
        focus = f"{plan.title}의 핵심 내용"
    description = f"{focus} 관련 핵심 수치·구조를 요약한 도표 또는 그래프"
    # 번호 없는 제목만 담는다 — 조립 단계에서 장 단위 일련번호를 붙인다. 제목은 담당
    # key_point로 짓는다: 절 제목을 그대로 쓰면 한 절의 그림 여러 개가 전부 같은
    # 이름이 된다(표 폴백 제목과 같은 문제, 2026-08-11 지적·2026-08-13 재발).
    caption = plan.key_points[index] if index < len(plan.key_points) else plan.title
    return Figure(caption=caption, description=description)


_SOURCE_TYPE_LABEL: dict[SourceType, str] = {
    SourceType.WEB_SEARCH: "웹",
    SourceType.UPLOAD: "업로드",
    SourceType.LIBRARY: "라이브러리",
}


# 최종장 제목 — 목차 항목과 장 헤딩이 같은 문자열을 쓰도록 한곳에서 고정한다.
REFERENCES_HEADING = "참고문헌"


def _visual_index_blocks(body: list[Block]) -> list[Block]:
    """표 목차·그림 목차 — 본문에 매겨진 캡션을 그대로 나열한다(실측 전 샘플 보유).

    쪽번호는 목차와 같은 방식이다 — 본문 캡션에 심은 책갈피를 가리키는 필드를 두고
    값은 문서를 여는 한컴이 채운다. 캡션 없는 표(약어 정리표)는 번호 접두가 없어
    자연히 빠진다.
    """
    tables = [(b.caption, b.caption_bookmark) for b in body if isinstance(b, Table)]
    figures = [(str(b.caption), b.caption_bookmark) for b in body if isinstance(b, Figure | Chart)]
    table_caps = [(cap, mark) for cap, mark in tables if cap.startswith("<표")]
    figure_caps = [(cap, mark) for cap, mark in figures if cap.startswith("<그림")]
    blocks: list[Block] = []
    if table_caps:
        blocks += [PageBreak(), Heading(level=1, text="표 목차")]
        blocks += [Paragraph(text=cap, indent=1, page_ref=mark) for cap, mark in table_caps]
    if figure_caps:
        blocks += [PageBreak(), Heading(level=1, text="그림 목차")]
        blocks += [Paragraph(text=cap, indent=1, page_ref=mark) for cap, mark in figure_caps]
    return blocks


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
    """보고서 전체 블록을 실제 순서(표지 → 요약문 → 목차 → 표·그림 목차 → 본문 →
    참고문헌)로 조립한다.

    - 표지(제목·기관·날짜) → 요약문(조립 시 생성·config 저장분, 없으면 생략).
    - 목차 — 렌더되는 장·절을 텍스트 번호로 나열(본문과 항상 일치). 이어서 표 목차·
      그림 목차(본문 캡션 그대로, 실측 전 샘플 보유 관행).
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
    # 본문은 원문 그대로 파싱한다 — (출처 n)은 작성·검증 단계의 근거 표식이지 납품물의
    # 표기가 아니라 걷어내지만(2026-08-11), 표 아래 출처 줄로 살릴 것을 먼저 골라내야
    # 하므로 블록 단계(_attach_table_sources → _strip_citation_blocks)에서 처리한다.
    contents = {plan.section_id: drafts[plan.section_id].content for plan in rendered}
    source_titles = {
        i: (src.title.strip() or "(제목 없음)") for i, src in enumerate(state.sources, start=1)
    }

    # 본문을 먼저 조립한다 — 표·그림 목차는 번호 매겨진 캡션을 알아야 만들 수 있다.
    body: list[Block] = []
    chapter_abbrs: dict[str, str] = {}
    current_chapter: int | None = None
    # 표·그림 번호는 장이 바뀔 때마다 1부터 다시 센다(<표 2-1>, <그림 2-1>…).
    chapter_table_no = 0
    chapter_figure_no = 0

    def flush_glossary() -> None:
        if chapter_abbrs:
            # 약어 정리는 장 끝 별도 페이지(쪽 나눔 후)에 배치한다(2026-08-05 확정).
            body.append(PageBreak())
            body.append(Heading(level=2, text="약어 정리"))
            entries = [
                [abbr, full, (glossary or {}).get(abbr, {}).get("desc", "")]
                for abbr, full in chapter_abbrs.items()
            ]
            body.extend(_glossary_tables(entries))
            chapter_abbrs.clear()

    for plan in rendered:
        if plan.chapter_number != current_chapter:
            if current_chapter is not None:
                flush_glossary()  # 앞 장의 약어 정리를 그 장 끝에 배치
            body.append(PageBreak())  # 챕터는 항상 새 쪽에서 시작
            ch_text = _chapter_heading_text(plan.chapter_number, ch_titles)
            body.append(Heading(level=1, text=ch_text, bookmark=_toc_bookmark(plan.chapter_number)))
            current_chapter = plan.chapter_number
            chapter_table_no = 0
            chapter_figure_no = 0
        title = f"{plan.chapter_number}.{plan.section_number} {plan.title}"
        body.append(
            Heading(
                level=2,
                text=title,
                bookmark=_toc_bookmark(plan.chapter_number, plan.section_number),
            )
        )
        content = _drop_duplicate_lead_heading(
            contents[plan.section_id], plan.chapter_number, plan.section_number
        )
        section_blocks = markdown_to_blocks(content)
        _attach_table_sources(section_blocks, source_titles)
        section_blocks = _strip_citation_blocks(section_blocks)
        visual_count = sum(1 for b in section_blocks if isinstance(b, Table | Chart))
        section_blocks += [
            _figure_placeholder(plan, i)
            for i in range(_figures_needed(content, visual_count, plan.key_points))
        ]
        chapter_table_no, chapter_figure_no = _number_visuals(
            section_blocks, plan.chapter_number, chapter_table_no, chapter_figure_no, plan.title
        )
        body.extend(section_blocks)
        _collect_abbreviations(contents[plan.section_id], chapter_abbrs)
    flush_glossary()  # 마지막 장

    # 전문(前文): 목차 → 표·그림 목차. (요약문은 r6에서 제거 — 최종 산출물에
    # 싣지 않기로 함, 2026-08-13 사용자 결정. 옛 config["summary"]는 그냥 무시된다.)
    blocks.append(PageBreak())
    blocks.append(Heading(level=1, text="목차"))
    toc_chapter: int | None = None
    for plan in rendered:
        if plan.chapter_number != toc_chapter:
            blocks.append(
                Paragraph(
                    text=_chapter_heading_text(plan.chapter_number, ch_titles),
                    page_ref=_toc_bookmark(plan.chapter_number),
                )
            )
            toc_chapter = plan.chapter_number
        entry = f"{plan.chapter_number}.{plan.section_number}  {plan.title}"
        blocks.append(
            Paragraph(
                text=entry,
                indent=1,
                page_ref=_toc_bookmark(plan.chapter_number, plan.section_number),
            )
        )
    # 참고문헌은 계획에 없는 최종장이라 목차에도 따로 실어야 본문과 어긋나지 않는다.
    if state.sources:
        blocks.append(Paragraph(text=REFERENCES_HEADING, page_ref=_REFERENCES_BOOKMARK))
    blocks.extend(_visual_index_blocks(body))

    blocks.extend(body)

    # 최종장: 참고문헌 — 프로젝트 자료 풀을 번호 목록으로 정리한다(자료 없으면 생략).
    if state.sources:
        blocks.append(PageBreak())
        blocks.append(Heading(level=1, text=REFERENCES_HEADING, bookmark=_REFERENCES_BOOKMARK))
        for i, src in enumerate(state.sources, start=1):
            # 왼쪽 정렬 — 서지 줄에는 줄바꿈이 안 되는 긴 URL이 들어 있어, 양쪽 정렬이면
            # 그 앞줄의 글자 사이가 벌어져 문단이 성글게 흩어진다(2026-08-12 지적).
            blocks.append(Paragraph(text=_source_entry(i, src), indent=1, align="LEFT"))
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

    path = out_dir / export_filename(state.project_id)
    build_report(
        report_blocks(state, glossary), path, template_path=template, apply_chrome=template is None
    )
    logger.info("export.hwpx_written", project_id=str(state.project_id), path=str(path))
    return path
