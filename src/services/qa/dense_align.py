"""교차언어 대목 정렬 — 어휘가 원리적으로 0점을 주는 구간만 임베딩으로 다시 본다.

근거 대조는 두 단계다: 검색이 청크를 고르고(BGE-M3, 다국어), 정렬이 그 청크 **안에서**
대목을 고른다(어휘 겹침). 그런데 코퍼스의 70%가 영문이라(2026-08-26 실측) 한글 주장과
영문 대목 사이에 공유 문자가 없다 — 한글 2-gram은 영문에 있을 수 없고 남는 건 수치와
영문 고유명사뿐이라, 의미가 정확히 대응해도 0점이 나온다:

    주장  "한국은 RE100 이행 관점에서 가장 까다로운 시장 중 하나로 평가되며…"
    대목  "Korea remains one of the most challenging markets for RE100 implementation."

검색을 굴리는 그 임베딩이 이건 한다. 그래서 **교차언어 구간에만** 덧댄다 — 한글 대 한글은
겹침이 이미 잘 하고(음성 대조로 검증됨), 대체하면 검증된 경로를 미검증으로 바꾸는 셈이다.

문턱(DENSE_THRESHOLD)은 보수적이다. 파일럿에서 실제 짝과 (같은 문서의) 무관한 대목의
점수 분포가 겹쳤다 — 같은 문서면 다 같은 주제라 주제 유사도가 "받치는가"를 완전히
가르지 못한다. 섞은 것의 최고보다 위로 잡아 확신 있는 것만 올리고, 나머지는 종전대로
"직접 확인하세요"로 남긴다. 아무 대목이나 확정하면 겹침 argmax가 만들던 거짓 확신을
새 옷 입혀 다시 만드는 것이다.

실패는 비치명이다 — 임베딩이 안 닿으면 종전 판정을 그대로 둔다(정보가 줄 뿐 틀리지 않는다).
"""

from __future__ import annotations

import math
from uuid import UUID

import structlog

from src.core.config import settings
from src.services.qa.alignment import (
    DENSE_THRESHOLD,
    MAX_CANDIDATES,
    ClaimAlignment,
    EvidenceSpan,
    _spans,
)

logger = structlog.get_logger(__name__)

# 한 요청에서 임베딩할 텍스트 상한 — 절 하나의 대목이 수천 개가 되는 병리에서
# 화면 응답이 통째로 멈추지 않게. 넘치면 앞에서 잘라 쓴다(부분 개선도 개선이다).
MAX_TEXTS = 600


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _targets(
    claims: list[ClaimAlignment],
    chunk_texts: dict[UUID, str],
) -> list[tuple[ClaimAlignment, list[tuple[UUID, int, int, str]]]]:
    """교차언어로 판정된 주장과 그 주장이 인용한 청크의 대목 목록."""
    out: list[tuple[ClaimAlignment, list[tuple[UUID, int, int, str]]]] = []
    for claim in claims:
        if claim.status != "crosslingual":
            continue
        pool: list[tuple[UUID, int, int, str]] = []
        for cid in claim.cited_chunk_ids:
            chunk = chunk_texts.get(cid)
            if not chunk:
                continue
            pool += [(cid, s, e, t) for s, e, t in _spans(chunk)]
        if pool:
            out.append((claim, pool))
    return out


async def refine_crosslingual(
    claims: list[ClaimAlignment],
    chunk_texts: dict[UUID, str],
    *,
    client=None,
) -> int:
    """교차언어 주장의 대목을 임베딩으로 확정한다. 승격한 건수를 돌려준다.

    claims를 제자리에서 고친다 — 문턱을 넘은 주장만 span이 바뀌고 dense_score가 채워져
    status가 'aligned'가 된다. 못 넘긴 주장은 손대지 않는다.
    """
    if not settings.dense_align_enabled:
        return 0
    targets = _targets(claims, chunk_texts)
    if not targets:
        return 0

    # 같은 텍스트를 여러 주장이 공유하므로 한 번만 임베딩한다.
    # ⚠️원격 임베딩 클라는 디스크 캐시를 **안 쓴다**(실측 2026-08-26: 같은 배치를 두 번
    # 불러도 cached 0/423). 재계산은 매번 전액이라, 대목 벡터를 색인 시점에 저장하는
    # 이관 전까지는 이 경로가 비싸다 - 플래그가 기본 off인 이유다.
    texts: list[str] = []
    seen: set[str] = set()
    for claim, pool in targets:
        for t in [claim.claim] + [x[3] for x in pool]:
            if t not in seen:
                seen.add(t)
                texts.append(t)
    if len(texts) > MAX_TEXTS:
        logger.info("dense_align.truncated", wanted=len(texts), used=MAX_TEXTS)
        texts = texts[:MAX_TEXTS]

    try:
        if client is None:
            from src.clients.embedding_factory import get_embedding_client

            client = get_embedding_client()
        results = await client.embed_batch(texts)
    except Exception:
        # 임베딩이 안 닿으면 종전 판정 그대로 — 화면이 조용히 덜 보여줄 뿐 틀리지 않는다.
        logger.warning("dense_align.failed", n_texts=len(texts), exc_info=True)
        return 0

    vec = {r.text: r.embedding for r in results}
    promoted = 0
    for claim, pool in targets:
        qv = vec.get(claim.claim)
        if qv is None:
            continue
        ranked: list[tuple[float, tuple[UUID, int, int, str]]] = []
        for item in pool:
            sv = vec.get(item[3])
            if sv is None:
                continue
            ranked.append((_cosine(qv, sv), item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        best = ranked[0] if ranked else None
        if best is None:
            continue
        # 후보는 문턱과 무관하게 싣는다 — 확정은 못 해도 "여기서 가져왔을 것 같다"를
        # 몇 개 보여주면 사람이 고를 수 있다. 어휘가 0점인 구간이라 이 순위가
        # 그 문장에 대한 **유일한 실마리**다.
        claim.candidates = [
            EvidenceSpan(
                chunk_id=c,
                number=claim.numbers[0] if claim.numbers else None,
                start=st,
                end=en,
                text=tx.strip(),
                score=0.0,
                comparable=False,
                dense_score=round(sc, 3),
            )
            for sc, (c, st, en, tx) in ranked[:MAX_CANDIDATES]
        ]
        score, (cid, start, end, text) = best
        if score < DENSE_THRESHOLD:
            continue
        claim.span = EvidenceSpan(
            chunk_id=cid,
            number=claim.numbers[0] if claim.numbers else None,
            start=start,
            end=end,
            text=text.strip(),
            # 어휘 점수는 이 대목에 대해 의미가 없다(교차언어라 0에 깔린다) - 0으로 두고
            # 판정은 dense_score가 한다. 두 자를 섞으면 화면이 어느 쪽을 보는지 모른다.
            score=0.0,
            comparable=False,
            dense_score=round(score, 3),
        )
        promoted += 1

    logger.info(
        "dense_align.done",
        n_crosslingual=len(targets),
        promoted=promoted,
        n_texts=len(texts),
    )
    return promoted
