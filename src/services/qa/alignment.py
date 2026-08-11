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

from src.services.qa.gate import claim_units, ungrounded_numbers

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
        return 0.0
    if not _HANGUL_RUN_RE.search(span):
        claim_tokens = {t: w for t, w in claim_tokens.items() if not t.startswith("k:")}
        if not claim_tokens:
            return 0.0
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


@dataclass
class ClaimAlignment:
    """본문 문장 하나의 근거 대조 결과."""

    claim: str
    numbers: list[int] = field(default_factory=list)
    span: EvidenceSpan | None = None
    ungrounded: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """aligned=대목 특정 · weak=겹침 희박 · unmatched=못 찾음 · uncited=표기 없음
        · crosslingual=근거가 외국어라 겹침으로는 판정 불가(LLM 판정 대상)."""
        if not self.numbers:
            return "uncited"
        if self.span is None:
            return "unmatched"
        if not self.span.comparable:
            return "crosslingual"
        if self.span.score >= ALIGNED_THRESHOLD:
            return "aligned"
        return "weak" if self.span.score >= WEAK_THRESHOLD else "unmatched"


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
        cited_text: list[str] = []
        for number in numbers:
            for cid in marker_chunks.get(number, ()):
                chunk = chunk_texts.get(cid)
                if not chunk:
                    continue
                cited_text.append(chunk)
                # 한글 주장을 외국어 근거로 재는 경우 - 점수는 대목을 가리키는 데만 쓴다.
                claim_has_hangul = bool(_HANGUL_RUN_RE.search(bare))
                for start, end, span_text in _spans(chunk):
                    comparable = not (claim_has_hangul and not _HANGUL_RUN_RE.search(span_text))
                    score = overlap_score(bare, span_text)
                    if best is None or score > best.score:
                        best = EvidenceSpan(
                            chunk_id=cid,
                            number=number,
                            start=start,
                            end=end,
                            text=span_text.strip(),
                            score=round(score, 3),
                            comparable=comparable,
                        )
        out.append(
            ClaimAlignment(
                claim=claim,
                numbers=numbers,
                span=best,
                # 수치는 대목이 아니라 인용 근거 전체를 상대로 본다(같은 자료의 다른
                # 줄에 있을 수 있다) — 게이트와 같은 판정을 쓴다.
                ungrounded=ungrounded_numbers(bare, "\n".join(cited_text)) if numbers else [],
            )
        )
    return out
