"""절 간 중복·떠 있는 상호참조 — 병렬 작성이 남기는 결함을 결정적으로 잡는다.

절을 병렬로 쓰면 각 절은 자기 근거만 본다. 같은 자료가 여러 절의 검색 상위에 걸리면
같은 문장이 여러 절에 그대로 실린다. 실측(완료 보고서 3건)에서 이게 실제 결함이었다 -
"글로벌 숏폼 시장은 2021년 432억 달러에서 2026년 1,350억 달러로 연평균 25.6% 성장"이
2.1·2.2·3.2·4.2 네 절에 거의 그대로 있었고, AI 반도체 보고서에서는 시장 전망 문장과
TSRI 설명 문장이 통째로 두 번씩 나왔다.

**LLM을 쓰지 않는다.** pm_verify 재측정이 같은 본문·temperature=0에서 44→54건으로
흔들렸고 카테고리 라벨은 몇 배로 요동했다. 이 검사는 작성에 되먹임할 수도 있는 신호라
노이즈가 본문을 오염시킨다. 어휘 겹침(alignment.overlap_score)만으로 충분하다는 것도
실측으로 확인했다 - 임계 0.8 이상은 전부 진짜 중복이었다.

임베딩도 안 쓴다. 설계 초안은 BGE-M3로 주장을 정규화하려 했지만, 실제 결함이 '의역된
같은 주장'이 아니라 '거의 그대로 복사된 문장'이라 글자 2-gram 겹침으로 잡힌다.
모델 로드도, API 비용도 0이다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import NamedTuple

from src.core.citations import numbers_in_order, strip_source_marks
from src.services.qa.alignment import _weighted_tokens
from src.services.qa.gate import claim_units

# 같은 문장이 통째로 복사된 수준. 실측에서 이 위는 전부 진짜 중복이었다.
DUPLICATE_THRESHOLD = 0.80
# 같은 사실을 다시 쓴 수준. 한 절에 한두 건은 자연스러워 건수로 판단한다.
SIMILAR_THRESHOLD = 0.65
# 너무 짧은 문장은 우연히 겹친다("이에 따른 대응이 필요함").
_MIN_UNIT_CHARS = 30
# 후보 생성에 쓸 토큰의 문서 빈도 상한 비율. 흔한 글자쌍("있다")으로 후보를 만들면
# 전수 비교와 다를 게 없어진다. 중복 문장은 드문 토큰(수치·영문·고유어)을 공유한다.
_MAX_DF_RATIO = 0.05
_MIN_MAX_DF = 3
# 후보 폭발 방어 — 35절 보고서에서 주장 단위가 3,000건을 넘는다.
_MAX_PAIRS = 400_000


class DuplicatePair(NamedTuple):
    """중복으로 묶인 두 절의 주장 한 쌍. score는 0~1."""

    score: float
    first_ref: str
    first_text: str
    second_ref: str
    second_text: str


# 술어가 '…N.N절 참조'인 문장은 내용이 아니라 위임이다. 포인터끼리 매칭하면 중복
# 오탐이 된다(2026-08-29 철강 정독: 4.2 "'협력모델'의 개념…은 1.1절 참조"가 1.2의
# 같은 포인터와 짝지어짐 — 실체는 1.1에만 있다). 출처 마커를 걷은 뒤 꼬리로 판정한다
# — "…기대됨(1.1절 참조)"처럼 실내용 문장에 참조가 덧붙은 것은 포인터가 아니다.
_POINTER_TAIL_RE = re.compile(r"\d{1,2}\s*\.\s*\d{1,2}\s*절\s*참조\s*[).\s]*$")


def _is_pointer(unit: str) -> bool:
    bare = strip_source_marks(unit).strip()
    # 괄호 안 참조("…기대됨(1.1절 참조)")는 실내용 문장의 덧붙임이지 위임이 아니다 —
    # 걷어낸 뒤에도 술어가 '…절 참조'로 끝나는 문장만 포인터로 본다.
    bare = re.sub(r"\([^()]*절\s*참조[^()]*\)\s*$", "", bare).strip()
    return bool(_POINTER_TAIL_RE.search(bare))


def _units_by_section(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """[(절 번호, 본문)] → [(절 번호, 주장 문장)]. 제목·표·구분선은 claim_units가 뺀다."""
    out: list[tuple[str, str]] = []
    for ref, content in sections:
        for unit in claim_units(content or ""):
            if len(unit) >= _MIN_UNIT_CHARS and not _is_pointer(unit):
                out.append((ref, unit))
    return out


def _candidate_pairs(token_sets: list[dict[str, float]]) -> set[tuple[int, int]]:
    """드문 토큰을 공유하는 쌍만 추린다.

    전수 비교는 3,000건이면 450만 쌍이라 QA 한 번에 몇 분이 든다. 중복 문장은 수치나
    영문 약어 같은 드문 토큰을 반드시 공유하므로, 그 토큰의 등장 목록 안에서만 쌍을
    만들면 결과가 같으면서 훨씬 싸다.
    """
    n = len(token_sets)
    postings: dict[str, list[int]] = defaultdict(list)
    for i, tokens in enumerate(token_sets):
        for token in tokens:
            postings[token].append(i)

    max_df = max(_MIN_MAX_DF, int(n * _MAX_DF_RATIO))
    pairs: set[tuple[int, int]] = set()
    for ids in postings.values():
        if len(ids) < 2 or len(ids) > max_df:
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                pairs.add((ids[a], ids[b]))
                if len(pairs) >= _MAX_PAIRS:
                    return pairs
    return pairs


def _similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """두 문장의 겹침 — 양방향 중 큰 쪽.

    짧은 문장이 긴 문장에 통째로 들어간 경우가 실제 중복의 전형이다(개조식 요약 한 줄이
    다른 절에서 문장으로 풀어 쓰인다). 한쪽 분모로만 재면 그 경우를 놓친다.
    """
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    hit = sum(w for t, w in small.items() if t in large)
    return max(hit / sum(a.values()), hit / sum(b.values()))


def duplicate_pairs(
    sections: list[tuple[str, str]], *, threshold: float = SIMILAR_THRESHOLD
) -> list[DuplicatePair]:
    """절 간 중복 문장 쌍을 점수 내림차순으로. 같은 절 안의 반복은 보지 않는다.

    Args:
        sections: [(절 번호 "2.1", 본문)] — 목차 순서대로.
        threshold: 이 점수 이상만 돌려준다.
    """
    units = _units_by_section(sections)
    if len(units) < 2:
        return []
    token_sets = [_weighted_tokens(text) for _ref, text in units]

    out: list[DuplicatePair] = []
    for i, j in _candidate_pairs(token_sets):
        if units[i][0] == units[j][0]:
            continue
        score = _similarity(token_sets[i], token_sets[j])
        if score >= threshold:
            first, second = (i, j) if _ref_key(units[i][0]) <= _ref_key(units[j][0]) else (j, i)
            out.append(
                DuplicatePair(
                    round(score, 3),
                    units[first][0],
                    units[first][1],
                    units[second][0],
                    units[second][1],
                )
            )
    out.sort(key=lambda p: (-p.score, p.first_ref, p.second_ref))
    return out


def _ref_key(ref: str) -> tuple[int, int]:
    parts = ref.split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return (0, 0)


class SourceMismatch(NamedTuple):
    """복사 수준 중복인데 두 쪽의 출처 번호가 서로소인 쌍."""

    pair: DuplicatePair
    first_sources: tuple[int, ...]
    second_sources: tuple[int, ...]


def source_mismatch_pairs(pairs: list[DuplicatePair]) -> list[SourceMismatch]:
    """같은 문장이 절을 옮기며 다른 출처 번호를 단 쌍 — 최소 한쪽은 오귀속이다.

    2026-08-29 철강 정독의 최대 병리: BF-BOF 공정 표 수치가 7개 절에서 출처 4벌,
    "포항 수소환원제철 개발센터" 문장이 4개 절에서 출처 4벌(6·8·31·28)로 실렸다.
    전역 번호 체계에서 복사된 같은 문장의 근거가 둘일 수는 없으므로, 겹치는 번호가
    하나도 없으면 확정 결함으로 본다(부분 겹침은 다중 인용의 정상 변형이라 둔다).
    """
    out: list[SourceMismatch] = []
    for p in pairs:
        if p.score < DUPLICATE_THRESHOLD:
            continue
        first = numbers_in_order(p.first_text)
        second = numbers_in_order(p.second_text)
        if first and second and not (set(first) & set(second)):
            out.append(SourceMismatch(p, tuple(first), tuple(second)))
    return out


def pointer_restatements(
    sections: list[tuple[str, str]], pairs: list[DuplicatePair]
) -> list[tuple[str, str, int]]:
    """'X.Y절 참조'로 넘겨 놓고 그 절 내용을 다시 쓴 절 — [(절, 참조 대상, 중복 건수)].

    실측(2026-08-29 철강 5.1): "동 특별법 및 시행령의 제정·시행 경과는 1.1절 참조."
    바로 다음 줄부터 1.1절 문장 14건이 축자 재등장했다. 참조와 재서술은 하나만 해야
    한다 — 위임했으면 본문을 비우고, 서술했으면 참조 문장을 지운다.
    """
    dup_count: dict[tuple[str, str], int] = defaultdict(int)
    for p in pairs:
        if p.score >= DUPLICATE_THRESHOLD:
            dup_count[(p.first_ref, p.second_ref)] += 1

    out: list[tuple[str, str, int]] = []
    for ref, content in sections:
        targets = {
            f"{int(m.group(1))}.{int(m.group(2))}" for m in _XREF_SECTION_RE.finditer(content or "")
        }
        for target in sorted(targets, key=_ref_key):
            n = dup_count.get((target, ref), 0)
            if n:
                out.append((ref, target, n))
    return out


# 본문이 다른 절을 가리키는 표기.
_XREF_SECTION_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\s*절")
# 장 참조는 반드시 "제N장"이어야 한다. "N장"만 받으면 수량이 걸린다 - 실측에서
# "추론 보드 10장~20장 구성"이 없는 장 참조로 잡혔다(2026-08-12).
_XREF_CHAPTER_RE = re.compile(r"제\s*(\d{1,2})\s*장")
# 앞을 가리키는 말. 보고서 첫 절에 나오면 가리킬 대상이 없다. "그에/이에 앞서"는
# 문서가 아니라 시간을 가리킨다(2026-08-28 v7 실측: "2050년이며, 그에 앞서 2030년·
# 2040년 …"이 첫 절 후방참조로 오탐).
_BACKREF_RE = re.compile(r"(?<!에 )(?<!보다 )앞서|앞 절|전술한|위에서 (?:살펴|언급|서술)|상기한")
# 이름으로 장을 가리키는 표기("시사점 장에서 전개", 2026-08-28 v7 실측: 그런 장이
# 없었다). 이름과 '장' 사이 띄어쓰기가 필수다 — 붙여 쓰면 "시장에서"·"공사장에서"
# 같은 일반 명사와 못 가르므로 보수적으로 놓친다.
_XREF_NAMED_CHAPTER_RE = re.compile(r"([가-힣]{2,10})\s+장(?:에서|으로|에)")
# 상대 지시어는 이름이 아니다 — 실재 여부를 제목으로 판정할 수 없다.
_NAMED_CHAPTER_STOP = {
    "이",
    "그",
    "본",
    "각",
    "다음",
    "이번",
    "해당",
    "별도",
    "별도의",
    "앞선",
    "마지막",
    "모든",
    "전체",
}


def dangling_references(
    sections: list[tuple[str, str]],
    chapter_titles: list[str] | None = None,
) -> list[tuple[str, str]]:
    """실재하지 않는 절·장을 가리키는 문구를 찾는다. [(절 번호, 설명)]

    병렬 작성에서는 각 절이 다른 절의 최종 번호를 모른 채 쓰인다. 목차가 바뀌거나
    모델이 번호를 지어내면 "3.4절에서 살펴본 바와 같이"가 남는데, 그 절이 없다.
    chapter_titles(목차의 장 제목들)가 오면 이름형 참조("시사점 장에서")도 대조한다 —
    없으면 그 축은 건너뛴다(제목 없이 실재를 판정할 수 없다).
    """
    known_sections = {ref for ref, _ in sections}
    known_chapters = {ref.split(".")[0] for ref in known_sections}
    first_ref = min(known_sections, key=_ref_key) if known_sections else ""
    title_bares = [t.replace(" ", "") for t in chapter_titles or [] if t]

    out: list[tuple[str, str]] = []
    for ref, content in sections:
        text = content or ""
        for m in _XREF_SECTION_RE.finditer(text):
            target = f"{int(m.group(1))}.{int(m.group(2))}"
            if target not in known_sections:
                out.append((ref, f"없는 절을 가리킴: {m.group()}"))
        for m in _XREF_CHAPTER_RE.finditer(text):
            if m.group(1) not in known_chapters:
                out.append((ref, f"없는 장을 가리킴: {m.group()}"))
        if title_bares:
            for m in _XREF_NAMED_CHAPTER_RE.finditer(text):
                name = m.group(1)
                if name in _NAMED_CHAPTER_STOP:
                    continue
                if not any(name.replace(" ", "") in t for t in title_bares):
                    out.append((ref, f"없는 장 이름을 가리킴: {m.group()}"))
        if ref == first_ref:
            found = _BACKREF_RE.search(text)
            if found:
                out.append((ref, f"첫 절인데 앞을 가리킴: {found.group()}"))
    return out
