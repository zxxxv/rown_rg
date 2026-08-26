"""문장 ↔ 근거 대목 정렬 — "이 문장은 원문의 어느 줄에서 나왔는가"를 코드로 답한다.

근거 추적 1단계는 문장을 청크까지 좁혔다. 청크는 수백~수천 자라 사람이 다시 읽어야
했고, 그래서 "정확히 어떤 부분"이라는 물음에는 여전히 답하지 못했다. 여기서 청크
안의 줄 단위까지 좁히고, 좁히지 못하면 그 사실 자체를 신호로 돌려준다.

설계 원칙은 게이트와 같다 — **LLM을 쓰지 않는다**. 판정이 매번 흔들리면 화면의 경고를
믿을 수 없고, 검토 화면은 절을 열 때마다 도는 경로라 비용도 얹을 수 없다. 대신 어휘
겹침을 잰다:

- 숫자는 가장 강한 신호다. 보고서가 지어내는 것도, 원문에서 그대로 옮기는 것도 숫자다.
- 한글은 조사가 붙어 어절이 안 맞는다("공급망의" vs "공급망") → 글자 2-gram으로 잰다.
- 라틴 문자(IRA·CAGR)는 그대로 두되 숫자 다음으로 무겁게 본다.

겹침이 낮다고 곧 창작은 아니다(의역이면 낮게 나온다). 그래서 이 값은 '확인 필요'
플래그이지 불합격 판정이 아니다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from src.services.qa.gate import (
    claim_units,
    normalize_haystack,
    normalize_number,
    number_in_text,
    numeric_mentions,
    significant_numbers,
    ungrounded_numbers,
)

# 수치 표기의 맨 숫자 — "3,200억"에서 "3,200"만 떼어 표시 토큰과 짝짓는다.
_LEADING_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 토큰 가중치 — 숫자 > 라틴 약어 > 한글 2-gram.
_W_NUMBER = 3.0
_W_LATIN = 1.5
_W_KOREAN = 1.0

_NUMBER_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
_HANGUL_RUN_RE = re.compile(r"[가-힣]{2,}")

# 근거 표기 두 형태를 다 받는다 — "[n]"(원문 직접 인용)과 "(출처 n)"·"(출처 3, 7)"
# (참고해 재작성). 한쪽만 읽으면 다른 쪽을 쓴 문장이 통째로 '표기 없음'으로 잘못
# 분류된다. (?!\()는 마크다운 링크 "[1](url)"의 라벨을 마커로 착각하지 않으려는 것.
# 문법은 곧 src/core/citations.py로 합칠 자리다 — 그 모듈이 커밋되면 그쪽을 쓴다.
_MARK_RE = re.compile(r"\(출처\s*(?P<source>\d+(?:\s*,\s*\d+)*)\s*\)|\[(?P<quote>\d+)\](?!\()")
_NUM_SPLIT_RE = re.compile(r"\s*,\s*")


def marker_numbers_in_order(text: str) -> list[int]:
    """근거 번호를 **등장 순서**대로 중복 없이 뽑는다(두 표기 모두).

    순서가 계약이다 — 작성기가 저장한 cited_chunk_ids가 이 순서와 위치로 대응하므로
    (candidates._extract_cited_ids), 정렬해 버리면 번호↔청크 매핑이 어긋난다.
    """
    out: list[int] = []
    seen: set[int] = set()
    for m in _MARK_RE.finditer(text):
        raw = m.group("source") or m.group("quote") or ""
        for token in _NUM_SPLIT_RE.split(raw.strip()):
            if not token:
                continue
            n = int(token)
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def marker_numbers(text: str) -> list[int]:
    """문장에 붙은 근거 번호 — 표시용(오름차순 중복 제거)."""
    return sorted(marker_numbers_in_order(text))


# 청크 안에서 후보 대목을 나누는 단위 — 줄이 길면 문장으로 한 번 더 자른다.
_LINE_SPLIT_RE = re.compile(r"\n+")
_SPAN_SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+")
_MAX_SPAN_CHARS = 220
_MIN_SPAN_CHARS = 8

# 대목을 특정했다고 볼 최소 겹침. 실측 분포(2026-08-11, 완성 절 4개·681개 주장):
# 사실 서술 절의 중앙값 0.39, 원문을 거의 옮긴 문장은 0.9 이상, 제언·종합 절은
# 중앙값 0.25까지 내려간다(직접 근거가 아니라 해석이므로 낮은 게 정상이다).
# 그래서 낮은 점수는 '틀렸다'가 아니라 '사람이 직접 확인할 곳'이라는 뜻이다.
ALIGNED_THRESHOLD = 0.30
WEAK_THRESHOLD = 0.15

# 다국어 임베딩으로 대목을 확정할 문턱. 파일럿(2026-08-26, crosslingual 표본 40건)에서
# 같은 문서·인용하지 않은 대목으로 섞은 어려운 음성 대조를 돌린 값에서 잡았다:
#   실제 짝 top-1  중앙 0.733 · 하위10% 0.593 · 최고 0.892
#   섞은 짝 top-1  중앙 0.617 · 하위10% 0.528 · 최고 0.765
# 분포가 겹친다(섞은 최고가 실제 중앙보다 높다) - 같은 문서의 대목은 다 같은 주제라
# 주제 유사도가 "이 문장을 받치는가"를 완전히 가르지는 못한다. 그래서 **섞은 것의
# 최고보다 위**로 잡아 확신 있는 것만 올린다. 덜 얻는 대신 거짓 확신을 안 만든다 -
# 문턱을 낮추려면 라벨을 만들어 정밀도·재현율 곡선을 먼저 그려야 한다.
DENSE_THRESHOLD = 0.78

# 사람에게 보여줄 후보 대목 수. 확정하지 못한 문장에 "여기서 가져왔을 것 같다"를
# 몇 개 내놓고 사람이 고르게 한다 — 낮은 점수 대목을 "근거"라 단정하면 거짓 확신이지만,
# 후보로 내놓으면 단정이 아니다. 그래서 **후보에는 문턱이 필요 없다**: 순위만 있으면 되고,
# 순위는 문서 간에 안정적이다(절대 점수는 코퍼스 언어 구성에 따라 크게 흔들린다 —
# 2026-08-27 실측: 같은 문턱에서 RE100 보고서 25% vs COMPA 6%).
MAX_CANDIDATES = 3

# 점수를 낼 수 없음(비교할 피처가 모자람). 0.0과 구분해야 한다 — 0.0은 "쟀는데 안 겹침",
# 이건 "잴 수가 없음"이고 사람이 할 일이 다르다.
NO_SCORE = -1.0
# 점수를 인정할 최소 피처 수 — 이보다 적으면 분모가 붕괴해 우연 일치가 1.00이 된다.
#
# 근거가 영문이면 한글 2-gram이 분모에서 빠지고 수치·라틴 토큰만 남는다. 남은 게 두세 개면
# 그것들이 청크에 있기만 해도 1.00이다. "거의 완벽한 일치"가 아니라 **셀 게 없는 것**인데
# 점수는 확신 있게 나온다 — 정보 없음이 정보 있음으로 표시되는 그 계급이다.
#
# 실측(2026-08-26, 탄소규제 보고서 1,317줄): 0.9 이상 150건의 피처 개수가 1개 5 · 2개 61 ·
# 3개 52 · 4개 이상 32건이고, 종류는 ln(라틴+수치) 133 · n 9 · l 5로 **한글 2-gram이 있는
# 건 3건뿐**이었다. 한글 대 한글은 짧은 문장도 2-gram이 열 개 넘게 나오므로 이 구간에
# 안 걸린다 — 4로 자르면 붕괴 덩어리(2~3개, 118건=79%)만 정확히 떨어진다.
#
# 4 이상인 32건은 남는다(수치 네 개가 다 맞는 경우 등). 그건 crosslingual 게이트와
# dense 유사도 경로가 맡을 몫이고, 여기서 막는 것은 **게이트 순서에 기대지 않는 하한**이다
# — 점수를 우선순위·prior로 쓰는 경로(판정 LLM 후보 정렬, 재랭킹)에 저 덩어리가
# 최고 확신으로 맨 앞에 서는 것을 산출식 안에서 끊는다.
_MIN_SUPPORT = 4


def _weighted_tokens(text: str) -> dict[str, float]:
    """텍스트 → {토큰: 가중치}. 한글은 글자 2-gram이라 조사 차이를 넘어 매칭된다."""
    out: dict[str, float] = {}
    for m in _NUMBER_TOKEN_RE.finditer(text):
        token = m.group().replace(",", "").rstrip("%")
        if token and (len(token.replace(".", "")) >= 2 or "." in token):
            out[f"n:{token}"] = _W_NUMBER
    for m in _LATIN_TOKEN_RE.finditer(text):
        out[f"l:{m.group().lower()}"] = _W_LATIN
    for run in _HANGUL_RUN_RE.findall(text):
        for i in range(len(run) - 1):
            out.setdefault(f"k:{run[i : i + 2]}", _W_KOREAN)
    return out


def weighted_tokens(text: str) -> dict[str, float]:
    """공개 진입점 - 검색 다양화(MMR)가 같은 자를 쓰도록 노출한다."""
    return _weighted_tokens(text)


def token_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """두 토큰 집합의 겹침 - 양방향 중 큰 쪽(0~1).

    짧은 문단이 긴 문단에 통째로 들어간 경우가 실제 중복의 전형이라 한쪽 분모로만
    재면 놓친다. cross_section._similarity와 같은 계산이다.
    """
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    hit = sum(w for t, w in small.items() if t in large)
    return max(hit / sum(a.values()), hit / sum(b.values()))


def overlap_score(claim: str, span: str) -> float:
    """주장 문장이 이 대목에 얼마나 담겨 있는가 (0~1). 비교할 게 없으면 -1.

    분모는 주장 쪽 가중치 합이다 — 근거가 길다고 점수가 깎이면 안 된다(긴 청크에
    짧은 근거 한 줄이 정답인 경우가 흔하다).

    **근거가 영문이면 한글 2-gram을 분모에서 뺀다.** 한글 주장의 가중치는 대부분
    한글 2-gram이 차지하는데 영문 근거에는 그게 있을 수 없다. 그대로 재면 점수가
    구조적으로 0.1 아래에 깔려 근거를 제대로 옮겨 쓴 문장까지 "불일치"가 된다
    (2026-08-12 실측: 영문 근거 866건 중 일치 1%·불일치 89%, 한글 근거는 67%/14%).
    수치와 영문 고유명사는 번역돼도 그대로 남으므로 그것만으로 잰다.

    **이 점수로 "뒷받침된다"를 선언하지는 않는다.** 교차언어에서는 남는 토큰이 수치와
    영문 고유명사뿐이라, 그것만 맞아도 1.00이 나온다. 실측(2026-08-12, 표본 20)에서
    그렇게 '일치'가 된 문장의 **40%가 실제로는 근거 없음**이었다 - "CAGR 30.33%로
    제시하며 가장 높은 성장률을 전망함"은 30.33%만 근거에 있고 '가장 높은'은 없다.
    한글로 덧붙인 해석이 통째로 미검증으로 남는다. 그래서 점수는 '어디를 보면 되는가'를
    가리키는 데만 쓰고, 판정은 align_section이 crosslingual로 넘긴다.
    """
    claim_tokens = _weighted_tokens(claim)
    if not claim_tokens:
        return NO_SCORE
    if not _HANGUL_RUN_RE.search(span):
        claim_tokens = {t: w for t, w in claim_tokens.items() if not t.startswith("k:")}
    # 셀 게 모자라면 점수를 내지 않는다 — 0.0으로 돌리면 "안 겹침"으로 읽히고,
    # 그대로 계산하면 우연 일치가 1.00으로 나온다(둘 다 사실이 아니다).
    if len(claim_tokens) < _MIN_SUPPORT:
        return NO_SCORE
    span_tokens = _weighted_tokens(span)
    hit = sum(w for t, w in claim_tokens.items() if t in span_tokens)
    return hit / sum(claim_tokens.values())


def _spans(chunk: str) -> list[tuple[int, int, str]]:
    """청크를 후보 대목(시작·끝·본문)으로 자른다. 위치는 원본 청크 기준 문자 오프셋."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for line in _LINE_SPLIT_RE.split(chunk):
        start = chunk.find(line, pos)
        if start < 0:  # 분해 규칙이 원문과 어긋나면 위치를 지어내지 않는다
            continue
        pos = start + len(line)
        if len(line) <= _MAX_SPAN_CHARS:
            if len(line.strip()) >= _MIN_SPAN_CHARS:
                out.append((start, start + len(line), line))
            continue
        inner = start
        for piece in _SPAN_SENTENCE_RE.split(line):
            at = chunk.find(piece, inner)
            if at < 0:
                continue
            inner = at + len(piece)
            if len(piece.strip()) >= _MIN_SPAN_CHARS:
                out.append((at, at + len(piece), piece))
    return out


@dataclass
class EvidenceSpan:
    """주장 하나가 가리킨 근거 대목."""

    chunk_id: UUID
    number: int | None
    start: int
    end: int
    text: str
    score: float
    # 이 대목의 언어로 주장을 검증할 수 있는가. 한글 주장 + 외국어 근거는 False -
    # 겹침 점수는 대목을 가리키는 데만 쓰고 뒷받침 판정은 LLM에 넘긴다.
    comparable: bool = True
    # 다국어 임베딩 코사인(services/qa/dense_align). 어휘가 원리적으로 0점을 주는
    # 교차언어 구간에만 채워진다 - 한글 대 한글은 겹침이 이미 잘 하므로 건드리지 않는다.
    dense_score: float | None = None


def _number_lines(chunk: str) -> list[tuple[int, int, str]]:
    """수치 탐색용 줄 분해 - _spans와 달리 길이 제한이 없다.

    수치의 단골 자리인 표 행은 220자를 훌쩍 넘겨 _spans가 후보에서 떨어뜨린다.
    위치를 가리키는 게 목적이라 짧다고 버릴 이유도, 길다고 자를 이유도 없다.
    """
    out: list[tuple[int, int, str]] = []
    pos = 0
    for line in _LINE_SPLIT_RE.split(chunk):
        start = chunk.find(line, pos)
        if start < 0:
            continue
        pos = start + len(line)
        if line.strip():
            out.append((start, start + len(line), line))
    return out


@dataclass
class NumberSpan:
    """본문 수치 하나가 근거 원문에서 발견된 자리(청크 문자 오프셋).

    ungrounded_numbers의 반대 방향이다 - "근거에 없다"만 알리지 않고, 있는 수치는
    어느 줄에서 왔는지 가리켜 화면이 바로 점프하게 한다. 판정이 아니라 위치다:
    같은 수치가 여러 줄에 있으면 주장과 어휘가 가장 겹치는 줄을 고를 뿐,
    그 줄이 주장을 뒷받침한다고 선언하지 않는다(교차언어 40% 은폐 실측의 교훈).
    """

    token: str  # 본문 표기 그대로("1,234억"이면 콤마 포함)
    chunk_id: UUID
    start: int
    end: int
    text: str


@dataclass
class ClaimAlignment:
    """본문 문장 하나의 근거 대조 결과."""

    claim: str
    numbers: list[int] = field(default_factory=list)
    span: EvidenceSpan | None = None
    # 이 주장이 인용한 청크 전부(중복 제거·등장 순). 판정(claim_verify)은 이 목록을
    # 근거로 삼는다 — 대목(span) 하나만 넘기면 한글 청크가 뽑히고, 정작 그 문장을
    # 받치는 영문 청크가 판정관에게 안 보인다(2026-08-24 COMPA 실측: 근거 불일치
    # 7건 전부 판정 경로였고 어휘 경로는 0건).
    cited_chunk_ids: list[UUID] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    # 근거에서 발견된 수치의 위치. ungrounded와 합치면 이 문장의 수치 전수가 된다.
    grounded: list[NumberSpan] = field(default_factory=list)
    # 인용 근거에 한글 대목이 하나라도 있는가. 전부 외국어면 겹침 0이어도 '불일치'가
    # 아니라 '잴 수 없음'이다 - 영문 근거를 직역한 문장이 대목조차 못 잡아 unmatched로
    # 새며 '근거 불일치' 경고가 부풀던 갭(2026-08-15 실측: 대표 예문 20건 정탐 0,
    # 오탐 15건의 주범이 직역 케이스).
    evidence_comparable: bool = True
    # 확정하지 못했을 때 사람이 고를 후보(점수 내림차순, span 제외). 어휘가 0점인
    # 교차언어에서는 비고 dense_align이 채운다 — 어휘 순위가 무의미한 구간이라
    # 억지로 채우면 아무 대목이나 후보로 올리는 꼴이 된다.
    candidates: list[EvidenceSpan] = field(default_factory=list)

    @property
    def status(self) -> str:
        """aligned=대목 특정 · weak=겹침 희박 · unmatched=못 찾음 · uncited=표기 없음
        · crosslingual=근거가 외국어라 겹침으로는 판정 불가(LLM 판정 대상)."""
        if not self.numbers:
            return "uncited"
        if self.span is None:
            return "unmatched" if self.evidence_comparable else "crosslingual"
        # 교차언어라도 임베딩이 문턱을 넘겨 확정한 대목은 '대목 특정'이다 - 어휘로는
        # 원리적으로 0점인 구간이라 이게 유일한 판별 수단이다.
        if self.span.dense_score is not None and self.span.dense_score >= DENSE_THRESHOLD:
            return "aligned"
        if not self.span.comparable:
            return "crosslingual"
        if self.span.score >= ALIGNED_THRESHOLD:
            return "aligned"
        if self.span.score >= WEAK_THRESHOLD:
            return "weak"
        # 겹침이 바닥이어도, 인용 근거에 외국어 청크가 섞여 있으면 '없다'가 아니라
        # '못 쟀다'이다 — 한글 청크의 겹침만 보고 단정하면 정작 그 문장을 받치는
        # 영문 청크가 통째로 무시된다(2026-08-24 COMPA 실측: 근거 불일치 7건 전부).
        return "unmatched" if self.evidence_comparable else "crosslingual"


def align_section(
    content: str,
    chunk_texts: dict[UUID, str],
    marker_chunks: dict[int, Sequence[UUID]],
) -> list[ClaimAlignment]:
    """절 본문의 각 주장을, 그 주장이 인용한 청크 안의 대목까지 좁힌다.

    marker_chunks는 본문 [n] → 청크 id(근거 추적이 만든 매핑). 한 문장에 번호가
    여럿이면 가장 잘 맞는 대목 하나만 남긴다 — 사람이 볼 것은 "어디를 보면 되는가"
    한 곳이지 후보 나열이 아니다.
    """
    out: list[ClaimAlignment] = []
    for claim in claim_units(content):
        numbers = marker_numbers(claim)
        # 마커 자체는 어휘가 아니다 — 점수 계산 전에 뗀다.
        bare = _MARK_RE.sub(" ", claim).strip()
        best: EvidenceSpan | None = None
        scored: list[EvidenceSpan] = []  # 후보 추림용 — 점수가 성립한 대목만 담는다
        cited_text: list[str] = []
        cited_chunks: list[tuple[UUID, str]] = []  # 수치 위치 탐색용(중복 제거)
        seen_cids: set[UUID] = set()
        for number in numbers:
            for cid in marker_chunks.get(number, ()):
                chunk = chunk_texts.get(cid)
                if not chunk:
                    continue
                if cid not in seen_cids:
                    seen_cids.add(cid)
                    cited_chunks.append((cid, chunk))
                cited_text.append(chunk)
                # 한글 주장을 외국어 근거로 재는 경우 - 점수는 대목을 가리키는 데만 쓴다.
                claim_has_hangul = bool(_HANGUL_RUN_RE.search(bare))
                for start, end, span_text in _spans(chunk):
                    comparable = not (claim_has_hangul and not _HANGUL_RUN_RE.search(span_text))
                    score = overlap_score(bare, span_text)
                    span = EvidenceSpan(
                        chunk_id=cid,
                        number=number,
                        start=start,
                        end=end,
                        text=span_text.strip(),
                        score=round(score, 3),
                        comparable=comparable,
                    )
                    # NO_SCORE(잴 수 없음)는 후보에서 뺀다 — 교차언어에서 전부 -1이라
                    # 담으면 순위가 없는 목록이 된다. 그 구간은 dense_align이 채운다.
                    if score > 0:
                        scored.append(span)
                    if best is None or score > best.score:
                        best = span
        # 수치는 대목이 아니라 인용 근거 전체를 상대로 본다(같은 자료의 다른
        # 줄에 있을 수 있다) — 게이트와 같은 판정을 쓴다.
        ungrounded = ungrounded_numbers(bare, "\n".join(cited_text)) if numbers else []
        joined_evidence = "\n".join(cited_text)
        scored.sort(key=lambda x: x.score, reverse=True)
        candidates = [
            x
            for x in scored
            if best is None or (x.chunk_id, x.start) != (best.chunk_id, best.start)
        ][:MAX_CANDIDATES]
        out.append(
            ClaimAlignment(
                claim=claim,
                numbers=numbers,
                span=best,
                candidates=candidates,
                cited_chunk_ids=[cid for cid, _text in cited_chunks],
                ungrounded=ungrounded,
                grounded=_grounded_spans(bare, cited_chunks, ungrounded),
                # 인용 근거가 **하나라도** 외국어면 겹침으로 '불일치'를 단정할 수 없다.
                # 종전에는 합친 글에 한글이 있기만 하면 잴 수 있다고 봤는데, 한글 청크와
                # 영문 청크를 함께 인용한 문장에서 한글 쪽 겹침만 재고 "근거에 없다"고
                # 단정했다 — 정작 그 문장을 받치는 건 영문 청크였다(2026-08-24 COMPA
                # 실측: 근거 불일치 7건 전부 이 꼴). 이런 문장은 판정(claim_verify)이
                # 근거 원문을 읽고 가리는 게 맞다.
                evidence_comparable=(
                    not joined_evidence.strip()
                    or all(_HANGUL_RUN_RE.search(text) for text in cited_text if text.strip())
                ),
            )
        )
    return out


def _grounded_spans(
    bare_claim: str,
    cited_chunks: list[tuple[UUID, str]],
    ungrounded: list[str],
) -> list[NumberSpan]:
    """주장 속 수치가 근거의 어느 줄에서 왔는지 - 무근거 판정된 수치는 건너뛴다.

    매칭은 ungrounded_numbers와 같은 자(콤마·후행 % 제거 후 부분문자열)로 반대
    방향을 잰다 - 거기서 '있음'이면 여기서 반드시 자리가 나온다. 같은 수치가 여러
    줄에 있으면 주장과 어휘가 가장 겹치는 줄을 고른다(동점이면 앞줄).
    """
    if not cited_chunks:
        return []
    skip = {normalize_number(t) for t in ungrounded}
    out: list[NumberSpan] = []
    seen: set[str] = set()
    # 표시 토큰은 기존 규약대로 맨 숫자(단위 없음)를 쓰고, 대조만 자리 단위를 붙인
    # 표기로 한다 — 표 검사·화면이 그 규약을 공유한다.
    mention_of = {
        normalize_number(_LEADING_NUMBER_RE.match(m).group(0)): m  # type: ignore[union-attr]
        for m in numeric_mentions(bare_claim)
        if _LEADING_NUMBER_RE.match(m)
    }
    for token in significant_numbers(bare_claim):
        norm = normalize_number(token)
        if not norm or norm in seen or norm in skip:
            continue
        seen.add(norm)
        mention = mention_of.get(norm, token)
        best: NumberSpan | None = None
        best_score = -1.0
        for cid, chunk in cited_chunks:
            for start, _end, line in _number_lines(chunk):
                # 자릿수 환산 표기까지 본다("70.5억"↔"USD 7.05 billion").
                if not number_in_text(mention, normalize_haystack(line)):
                    continue
                # 웹 원문은 문단이 한 줄이다 - 줄째로 강조하면 문단 전체가 칠해진다
                # (2026-08-14 화면 검증). 수치가 든 문장까지 좁힌다.
                s, e, seg = _narrow_to_sentence(line, start, norm)
                score = overlap_score(bare_claim, seg)
                # 여기 후보는 number_in_text가 이미 확인한 것들이라 점수는 순위용이다.
                # NO_SCORE(-1)가 나와도 버리면 안 된다 — 수치는 실제로 그 줄에 있다.
                if best is None or score > best_score:
                    best_score = score
                    best = NumberSpan(token=token, chunk_id=cid, start=s, end=e, text=seg.strip())
        if best is not None:
            out.append(best)
    return out


def _narrow_to_sentence(line: str, line_start: int, norm: str) -> tuple[int, int, str]:
    """줄 안에서 정규화 수치를 품은 문장 하나로 좁힌다. 못 찾으면 줄 전체.

    표 행처럼 문장 경계가 없는 줄은 그대로 남는다 - 좁히기는 보정이지 필터가 아니다.
    """
    inner = 0
    for piece in _SPAN_SENTENCE_RE.split(line):
        at = line.find(piece, inner)
        if at < 0:
            continue
        inner = at + len(piece)
        if norm in piece.replace(",", ""):
            return line_start + at, line_start + at + len(piece), piece
    return line_start, line_start + len(line), line
