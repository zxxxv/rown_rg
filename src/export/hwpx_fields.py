"""HWPX 필드 — 목차 쪽번호를 한컴이 채우게 하는 책갈피·상호참조, 그리고 점선 탭.

**쪽번호 값을 우리가 계산하지 않는다.** 어느 쪽에서 페이지가 넘어가는지는 폰트 메트릭·
금칙처리·표 행 높이·문단 나눔 규칙을 한컴과 똑같이 재현해야 나오는 값이다. 한 군데만
어긋나면 목차가 틀린 쪽을 가리키는데, 그건 번호가 없느니만 못하다. 그래서 문서에는
"이 책갈피가 있는 쪽 번호를 여기 넣어라"는 **필드**만 심고, 값은 문서를 여는 한컴이 채운다.

XML 형식은 지어내지 않았다. 두 실물에서 그대로 뜯어냈다:
- 책갈피·상호참조: 사용자가 한컴으로 만들어 준 샘플 ``C:\\github\\rown\\123.hwpx``
- 목차 점선 탭: 실납품 보고서 ``250822_산업기술 알키미스트 …보고서.hwpx``
  (그 보고서의 목차는 제목 + 오른쪽 정렬 점선 탭까지만 있고 쪽번호는 없다 —
  쪽번호가 안 채워져도 그 관례와 같은 모습으로 남는 것이 우리 폴백이다)

한컴 기본 ``TABLEOFCONTENTS`` 필드는 쓸 수 없다. 그건 **개요 수준**으로 목차를 모으는데,
우리는 한컴이 제목 앞에 "1." "2."를 멋대로 붙이는 것을 막으려 헤딩에 개요 수준을 일부러
주지 않는다(hwpx_writer 모듈 주석). 그래서 목차는 코드가 조립하고 쪽번호 자리에만
상호참조를 심는다.
"""

from __future__ import annotations

import itertools
from typing import Any

_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
_HWP_UNITS_PER_MM = 7200 / 25.4

# 필드 종류를 가리키는 고정 id — 샘플에서 종류마다 같은 값을 쓴다(begin/end가 공유).
_BOOKMARK_FIELD_ID = "627207531"
_CROSSREF_FIELD_ID = "628650598"

# 필드 인스턴스 id — 문서 안에서만 유일하면 된다. 샘플과 같은 대역에서 뽑는다.
_field_ids = itertools.count(1165117830)

# 목차 탭 — 실납품 보고서에서 그대로 가져온 값(점선 유형·오른쪽 정렬).
# tabPr의 leader/type 이름과 본문 tab의 숫자 값이 짝을 이룬다: DASH↔3, RIGHT↔2.
_TAB_LEADER_NAME = "DASH"
_TAB_LEADER_CODE = "3"
_TAB_TYPE_NAME = "RIGHT"
_TAB_TYPE_CODE = "2"
_HWP_UNIT_CHAR_NS = "http://www.hancom.co.kr/hwpml/2016/HwpUnitChar"


def next_field_id() -> str:
    return str(next(_field_ids))


def _sub(parent: Any, tag: str, attrs: dict[str, str] | None = None) -> Any:
    child = parent.makeelement(tag, attrs or {})
    parent.append(child)
    return child


def _field_begin_ctrl(run: Any, *, field_id: str, kind: str, name: str, dirty: str) -> Any:
    """``<hp:ctrl><hp:fieldBegin …>``를 만들어 돌려준다(파라미터는 호출부가 채운다)."""
    ctrl = run.makeelement(f"{_HP}ctrl", {})
    begin = _sub(
        ctrl,
        f"{_HP}fieldBegin",
        {
            "id": field_id,
            "type": kind,
            "name": name,
            "editable": "0",
            "dirty": dirty,
            "zorder": "-1",
            "fieldid": _BOOKMARK_FIELD_ID if kind == "BOOKMARK" else _CROSSREF_FIELD_ID,
        },
    )
    return ctrl, begin


def _field_end_ctrl(run: Any, *, field_id: str, kind: str) -> Any:
    ctrl = run.makeelement(f"{_HP}ctrl", {})
    _sub(
        ctrl,
        f"{_HP}fieldEnd",
        {
            "beginIDRef": field_id,
            "fieldid": _BOOKMARK_FIELD_ID if kind == "BOOKMARK" else _CROSSREF_FIELD_ID,
        },
    )
    return ctrl


def wrap_bookmark(paragraph: Any, name: str) -> None:
    """문단의 글자를 책갈피로 감싼다 — 상호참조가 가리킬 표적.

    문단 첫 run의 ``<hp:t>`` 앞뒤에 필드 시작/끝을 끼운다. 글이 없는 문단은 감쌀 것이
    없으므로 그냥 둔다(표적이 없으면 상호참조도 안 만든다).
    """
    element = paragraph.element
    run = element.find(f"{_HP}run")
    if run is None or run.find(f"{_HP}t") is None:
        return
    field_id = next_field_id()
    ctrl_begin, begin = _field_begin_ctrl(
        run, field_id=field_id, kind="BOOKMARK", name=name, dirty="1"
    )
    params = _sub(begin, f"{_HP}parameters", {"cnt": "1", "name": ""})
    _sub(params, f"{_HP}integerParam", {"name": "Prop"}).text = "2"
    run.insert(0, ctrl_begin)
    run.append(_field_end_ctrl(run, field_id=field_id, kind="BOOKMARK"))


def append_page_ref(paragraph: Any, name: str, *, char_pr_id_ref: str | None = None) -> None:
    """문단 끝에 "이 책갈피의 쪽번호" 필드를 붙인다.

    캐시 값은 비워 둔다. 한컴이 열면서 갱신하면 번호가 채워지고, 갱신하지 않으면
    아무것도 안 보인다 — 틀린 번호를 보여 주느니 안 보이는 편이 낫다(그 상태가 곧
    실납품 보고서의 목차 모습이다).
    """
    element = paragraph.element
    last_run = element.findall(f"{_HP}run")
    attrs = {}
    if char_pr_id_ref is not None:
        attrs["charPrIDRef"] = char_pr_id_ref
    elif last_run:
        ref = last_run[-1].get("charPrIDRef")
        if ref is not None:
            attrs["charPrIDRef"] = ref
    run = element.makeelement(f"{_HP}run", attrs)

    field_id = next_field_id()
    ctrl_begin, begin = _field_begin_ctrl(
        run, field_id=field_id, kind="CROSSREF", name="", dirty="0"
    )
    params = _sub(begin, f"{_HP}parameters", {"cnt": "8", "name": ""})
    _sub(params, f"{_HP}booleanParam", {"name": "Fiexde"}).text = "1"
    _sub(params, f"{_HP}integerParam", {"name": "Prop"}).text = "0"
    _sub(params, f"{_HP}stringParam", {"name": "Command"}).text = f"?{name};6;0;0;0;"
    _sub(params, f"{_HP}stringParam", {"name": "RefPath"}).text = f"?{name};"
    _sub(params, f"{_HP}stringParam", {"name": "RefType"}).text = "TARGET_BOOKMARK"
    # 이 한 줄이 "쪽번호"를 뜻한다 — 다른 값이면 제목·번호 등 다른 것을 가져온다.
    _sub(params, f"{_HP}stringParam", {"name": "RefContentType"}).text = "OBJECT_TYPE_PAGE"
    _sub(params, f"{_HP}booleanParam", {"name": "RefHyperLink"}).text = "false"
    _sub(params, f"{_HP}stringParam", {"name": "RefOpenType"}).text = "HWPHYPERLINK_JUMP_CURRENTTAB"
    run.append(ctrl_begin)
    _sub(run, f"{_HP}t")  # 한컴이 채울 자리(비워 둔다)
    run.append(_field_end_ctrl(run, field_id=field_id, kind="CROSSREF"))

    # 줄 배치 캐시(linesegarray)는 항상 문단 끝에 온다 — 그 앞에 넣는다.
    lineseg = element.find(f"{_HP}linesegarray")
    if lineseg is not None:
        element.insert(list(element).index(lineseg), run)
    else:
        element.append(run)


def append_leader_tab(paragraph: Any, *, width_hwp: int) -> None:
    """문단 끝 글자 뒤에 오른쪽 정렬 점선 탭을 넣는다(목차 줄의 점선).

    탭은 ``<hp:t>`` **안에** 들어간다 — 실납품 보고서가 그렇게 쓴다.
    """
    element = paragraph.element
    runs = element.findall(f"{_HP}run")
    if not runs:
        return
    texts = runs[-1].findall(f"{_HP}t")
    target = texts[-1] if texts else _sub(runs[-1], f"{_HP}t")
    _sub(
        target,
        f"{_HP}tab",
        {"width": str(width_hwp), "leader": _TAB_LEADER_CODE, "type": _TAB_TYPE_CODE},
    )


def ensure_toc_tab_pr(doc: Any, *, right_margin_mm: float) -> str:
    """오른쪽 끝에 점선 탭을 세운 tabPr를 만들고 그 id를 돌려준다(이미 있으면 재사용).

    문단이 ``tabPrIDRef``로 이 정의를 가리켜야 탭이 오른쪽 여백까지 늘어난다.
    """
    header = doc.headers[0]
    root = header.element
    tab_props = root.find(f".//{_HH}tabProperties")
    if tab_props is None:
        ref_list = root.find(f".//{_HH}refList")
        if ref_list is None:
            return "0"
        tab_props = _sub(ref_list, f"{_HH}tabProperties", {"itemCnt": "0"})

    existing = tab_props.findall(f"{_HH}tabPr")
    pos = round(right_margin_mm * _HWP_UNITS_PER_MM)
    # 규격에 없는 표식용 속성은 넣지 않는다(한컴이 어떻게 다룰지 모른다). 이미 만든
    # 정의인지는 생김새로 알아본다 — 같은 자리·같은 유형의 탭이면 그것을 다시 쓴다.
    for tab_pr in existing:
        item = tab_pr.find(f".//{_HH}tabItem")
        if (
            item is not None
            and item.get("type") == _TAB_TYPE_NAME
            and item.get("leader") == _TAB_LEADER_NAME
            and item.get("pos") == str(pos)
        ):
            return tab_pr.get("id", "0")

    used = {int(t.get("id", "0")) for t in existing}
    new_id = str(max(used) + 1 if used else 1)
    tab_pr = _sub(
        tab_props,
        f"{_HH}tabPr",
        {"id": new_id, "autoTabLeft": "0", "autoTabRight": "0"},
    )
    switch = _sub(tab_pr, f"{_HP}switch")
    # 네임스페이스가 붙은 속성은 Clark 표기로 넣어야 한다(lxml이 접두사 문자열을 거부한다).
    case = _sub(switch, f"{_HP}case", {f"{_HP}required-namespace": _HWP_UNIT_CHAR_NS})
    _sub(
        case,
        f"{_HH}tabItem",
        {
            "pos": str(pos),
            "type": _TAB_TYPE_NAME,
            "leader": _TAB_LEADER_NAME,
            "unit": "HWPUNIT",
        },
    )
    default = _sub(switch, f"{_HP}default")
    # 기본 분기는 실물에서 HWPUNIT의 정확히 두 배 값을 쓴다(48500 ↔ 97000).
    _sub(
        default,
        f"{_HH}tabItem",
        {"pos": str(pos * 2), "type": _TAB_TYPE_NAME, "leader": _TAB_LEADER_NAME},
    )
    tab_props.set("itemCnt", str(len(tab_props.findall(f"{_HH}tabPr"))))
    header.mark_dirty()
    return new_id


def set_tab_pr(doc: Any, paragraph: Any, tab_pr_id: str) -> None:
    """문단이 쓰는 paraPr에 tabPrIDRef를 물린다.

    서식이 같은 문단은 paraPr를 함께 쓰므로 같은 서식의 다른 문단에도 이 탭 정의가
    걸린다. 탭 정의는 **탭 문자가 있을 때만** 쓰이고 본문 문단에는 탭이 없으니 보이는
    변화는 없다 — 문단마다 paraPr를 복제하면 서식 정의만 불어난다.
    """
    para_pr_id = paragraph.para_pr_id_ref
    if para_pr_id is None:
        return
    header = doc.headers[0]
    for para_pr in header.element.iter(f"{_HH}paraPr"):
        if para_pr.get("id") == str(para_pr_id):
            para_pr.set("tabPrIDRef", tab_pr_id)
            header.mark_dirty()
            return
