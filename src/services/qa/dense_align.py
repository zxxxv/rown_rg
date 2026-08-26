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

from uuid import UUID

import numpy as np
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


def _cosine_many(query: list[float], rows: list[list[float]]) -> list[float]:
    """주장 하나 대 대목 여럿의 코사인 — 한 번의 행렬곱으로.

    순수 파이썬으로 재면 1024차원 × (주장 × 대목)이 그대로 곱셈 횟수가 된다. 대목
    벡터를 보관하기 전에는 임베딩 상한(MAX_TEXTS)이 비교 수를 눌러 줘서 안 보였는데,
    보관분을 쓰면 그 상한에 안 걸려 비교가 늘고 이 자리가 병목이 됐다(실측
    2026-08-27: 한 절이 5.9초에서 13.1초로 뒤집혔다 — 임베딩을 아꼈는데 더 느려졌다).
    """
    if not rows:
        return []
    mat = np.asarray(rows, dtype=np.float32)
    q = np.asarray(query, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1)
    qn = float(np.linalg.norm(q))
    if qn == 0:
        return [0.0] * len(rows)
    # 길이가 0인 벡터는 방향이 없다 - 나누지 않고 0점을 준다.
    safe = np.where(norms == 0, 1.0, norms)
    scores = (mat @ q) / (safe * qn)
    return np.where(norms == 0, 0.0, scores).tolist()


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
    session=None,
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

    # 색인 때 만들어 둔 대목 벡터를 먼저 읽는다 - 절당 임베딩 154건 중 대목이 ~123건이라
    # 여기서 8할이 조회로 바뀐다(실측 2026-08-27). 없으면 종전대로 그 자리에서 만든다.
    stored: dict[tuple[UUID, int], list[float]] = {}
    if session is not None:
        cited = {
            cid: chunk_texts[cid] for _, pool in targets for cid, *_ in pool if cid in chunk_texts
        }
        try:
            from src.services.qa.span_vectors import load_for_chunks

            stored = await load_for_chunks(session, cited)
        except Exception:
            # 보관분을 못 읽어도 계산 경로가 살아 있다 - 느려질 뿐 틀리지 않는다.
            logger.warning("dense_align.stored_load_failed", exc_info=True)

    # 같은 텍스트를 여러 주장이 공유하므로 한 번만 임베딩한다.
    # ⚠️원격 임베딩 클라는 디스크 캐시를 **안 쓴다**(실측 2026-08-26: 같은 배치를 두 번
    # 불러도 cached 0/423). 보관분이 없는 대목은 여기서 전액을 다시 낸다.
    #
    # **주장을 먼저 싣는다.** 상한에 걸려 잘리는 건 뒤쪽인데, 거기 주장이 있으면 그
    # 문장은 대목도 후보도 못 받고 통째로 빠진다. 대목이 잘리면 그 주장의 후보가 몇 개
    # 줄 뿐이다 - 같은 상한이라면 손실이 작은 쪽을 자르는 게 맞다.
    texts: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        if text not in seen:
            seen.add(text)
            texts.append(text)

    for claim, _ in targets:
        # 주장은 사람이 고쳐 쓰는 것이라 보관 대상이 아니다 - 늘 그때그때 만든다.
        _add(claim.claim)
    n_claims = len(texts)
    for _, pool in targets:
        for cid, st, _, text in pool:
            if (cid, st) not in stored:
                _add(text)
    if len(texts) > MAX_TEXTS:
        logger.info("dense_align.truncated", wanted=len(texts), used=MAX_TEXTS, n_claims=n_claims)
        texts = texts[:MAX_TEXTS]

    vec: dict[str, list[float]] = {}
    if texts:
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
        items: list[tuple[UUID, int, int, str]] = []
        rows: list[list[float]] = []
        for item in pool:
            # 보관분이 먼저 - 같은 모델이 만든 같은 벡터라 방금 만든 것과 구별되지 않는다.
            sv = stored.get((item[0], item[1])) or vec.get(item[3])
            if sv is None:
                continue
            items.append(item)
            rows.append(sv)
        ranked = list(zip(_cosine_many(qv, rows), items, strict=True))
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
        n_stored=len(stored),
    )
    return promoted
