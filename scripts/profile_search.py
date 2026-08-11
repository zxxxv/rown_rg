"""검색 파이프라인 단계별 소요시간 프로파일러.

"검색이 느리다"를 "어느 단계가 느리다"로 바꾸는 것이 유일한 목적이다. 측정 없이
리랭커부터 손대면 엉뚱한 데를 고칠 수 있다 — 이 스크립트가 그 판단의 전제다.

계측 구간: 쿼리 임베딩 / 벡터 검색 / 키워드 검색 / RRF 융합 / 리랭킹(후보 10·20·50).
각 질의를 워밍업 1회 + 본측정 3회 돌려 중앙값을 쓴다. 첫 호출과 이후 호출 차이가
크면 ONNX 세션이 매번 새로 생기고 있다는 신호라 따로 경고한다.

사용:
    uv run python scripts/profile_search.py [--project-id UUID] [--queries eval/queries.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

# 샘플 질의 — eval/queries.yaml이 없을 때 쓴다(그 사실을 출력한다).
SAMPLE_QUERIES = [
    "차세대 AI 반도체 시장 전망과 성장률",
    "반도체 공급망 리스크와 수출 규제",
    "국가 R&D 투자 추이와 부처별 배분",
    "예비타당성조사의 경제성 분석 방법",
    "인공지능 반도체 인력 양성 정책",
]
RERANK_CANDIDATE_COUNTS = (10, 20, 50)
REPEATS = 3


class Timer:
    """구간별 누적 계측 — 단계 합이 엔드투엔드와 맞는지 검증할 수 있게 모아 둔다."""

    def __init__(self) -> None:
        self.spans: dict[str, list[float]] = {}

    @contextmanager
    def measure(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.spans.setdefault(name, []).append((time.perf_counter() - start) * 1000)

    def median(self, name: str) -> float:
        return statistics.median(self.spans.get(name, [0.0]))


def _load_queries(path: Path | None) -> tuple[list[str], bool]:
    """(질의 목록, 폴백 여부). yaml 스키마는 text 필드만 읽는다."""
    if path is None or not path.exists():
        return SAMPLE_QUERIES, True
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("queries") or []
    texts = [str(r.get("text", "")).strip() for r in rows if str(r.get("text", "")).strip()]
    return (texts, False) if texts else (SAMPLE_QUERIES, True)


def _runtime_info() -> dict[str, Any]:
    """스레드 설정·코어 수·메모리 — 튜닝 판단의 배경값."""
    import onnxruntime as ort
    import psutil

    proc = psutil.Process()
    return {
        "물리 코어": psutil.cpu_count(logical=False),
        "논리 코어": psutil.cpu_count(logical=True),
        "onnxruntime": ort.__version__,
        "peak RSS MB": round(proc.memory_info().rss / 1024 / 1024, 1),
    }


async def _profile_query(
    query: str, project_id: UUID, timer: Timer, session_ids: set[int]
) -> dict[str, Any]:
    """질의 1건의 단계별 계측. 반환값은 진단용 부가 정보."""
    from src.clients.embedding_factory import get_embedding_client
    from src.db.session import async_session_maker
    from src.services.retrieval._keyword import KeywordSearchClient
    from src.services.retrieval._semantic import SemanticSearchClient
    from src.services.retrieval.hybrid import HybridSearchClient

    embedder = get_embedding_client()
    session_ids.add(id(getattr(embedder, "_session", embedder)))

    with timer.measure("1. 쿼리 임베딩"):
        await embedder.embed(query)

    semantic = SemanticSearchClient(async_session_maker, embedder)
    keyword = KeywordSearchClient(async_session_maker)

    with timer.measure("2. 벡터 검색"):
        dense_hits = await semantic.search(query, project_id, top_k=50)
    with timer.measure("3. 키워드 검색"):
        sparse_hits = await keyword.search(query, project_id, top_k=50)
    with timer.measure("4. RRF 융합"):
        hybrid = HybridSearchClient(semantic, keyword)
        fused = hybrid._fuse(dense_hits, sparse_hits)  # noqa: SLF001

    counts: dict[int, int] = {}
    from src.core.config import settings

    if settings.reranker_enabled:
        from src.clients.reranker_factory import get_reranker_client

        reranker = get_reranker_client()
        session_ids.add(id(getattr(reranker, "_session", reranker)))
        for n in RERANK_CANDIDATE_COUNTS:
            cand = fused[:n]
            counts[n] = len(cand)
            with timer.measure(f"5. 리랭킹 (후보 {n})"):
                if cand:
                    await reranker.score_pairs(query, [c.content for c in cand])
    return {
        "dense": len(dense_hits),
        "sparse": len(sparse_hits),
        "fused": len(fused),
        "rerank_counts": counts,
    }


def _verdicts(timer: Timer, first_ms: float, later_ms: float, session_ids: set[int]) -> list[str]:
    """측정값 → 판정 문구. 사람이 다음에 무엇을 할지 바로 알게 한다."""
    totals = {k: timer.median(k) for k in timer.spans}
    # 리랭킹은 후보 수별로 여러 구간이라 대표값(가장 큰 후보)만 병목 비교에 쓴다.
    compare = {k: v for k, v in totals.items() if not k.startswith("5.")}
    rerank_key = f"5. 리랭킹 (후보 {max(RERANK_CANDIDATE_COUNTS)})"
    if rerank_key in totals:
        compare[rerank_key] = totals[rerank_key]
    total = sum(compare.values()) or 1.0
    top = max(compare, key=lambda k: compare[k])
    out = [f"- 최대 병목: **{top}** ({compare[top]:.0f}ms, 전체의 {compare[top] / total:.0%})"]
    if rerank_key in compare and compare[rerank_key] / total >= 0.5:
        out.append("- 리랭킹이 절반을 넘는다 → **리랭커 경량화(작업 6) 우선**")
    if later_ms > 0 and first_ms / max(later_ms, 0.001) >= 5:
        out.append(
            f"- 첫 임베딩 호출이 이후의 {first_ms / later_ms:.1f}배 →"
            " **세션 재사용 미적용 의심**(워밍업·싱글턴 확인)"
        )
    if len(session_ids) > 1:
        out.append(f"- 세션 객체가 호출 간 달라진다({len(session_ids)}종) → **싱글턴 아님**")
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="검색 파이프라인 단계별 프로파일러")
    parser.add_argument("--project-id", help="대상 프로젝트(생략 시 청크가 가장 많은 것)")
    parser.add_argument("--queries", default="eval/queries.yaml")
    args = parser.parse_args()

    from sqlalchemy import text as sql_text

    from src.db.session import async_session_maker

    queries, fell_back = _load_queries(Path(args.queries))
    if fell_back:
        print(f"[안내] {args.queries} 없음 → 샘플 질의 {len(queries)}건으로 진행")

    try:
        async with async_session_maker() as session:
            if args.project_id:
                pid = UUID(args.project_id)
            else:
                row = (
                    await session.execute(
                        sql_text(
                            "SELECT project_id, count(*) n FROM chunks WHERE project_id IS NOT NULL"
                            " GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
                        )
                    )
                ).first()
                if row is None:
                    print("[오류] 색인된 청크가 없습니다.")
                    return 1
                pid = row[0]
            n_chunks = (
                await session.execute(
                    sql_text("SELECT count(*) FROM chunks WHERE project_id = :p"), {"p": str(pid)}
                )
            ).scalar_one()
    except Exception as exc:
        print(f"[오류] DB 연결/조회 실패: {type(exc).__name__}: {exc}")
        return 1

    if not n_chunks:
        print(f"[오류] 프로젝트 {pid}: 인덱스 비어 있음(청크 0건)")
        return 1
    print(f"[대상] project={pid} · 청크 {n_chunks:,}건 · 질의 {len(queries)}건")

    session_ids: set[int] = set()
    warm = Timer()
    await _profile_query(queries[0], pid, warm, session_ids)  # 워밍업(버림)
    first_ms = warm.median("1. 쿼리 임베딩")

    timer = Timer()
    info: dict[str, Any] = {}
    for _ in range(REPEATS):
        for q in queries:
            info = await _profile_query(q, pid, timer, session_ids)
    later_ms = timer.median("1. 쿼리 임베딩")

    rows = []
    for name in sorted(timer.spans):
        vals = sorted(timer.spans[name])
        p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
        rows.append((name, statistics.mean(vals), statistics.median(vals), p95))
    total = sum(r[2] for r in rows if not r[0].startswith("5.")) + timer.median(
        f"5. 리랭킹 (후보 {max(RERANK_CANDIDATE_COUNTS)})"
    )

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path("reports") / f"search_profile_{stamp}.md"
    out.parent.mkdir(exist_ok=True)
    lines = [
        f"# 검색 파이프라인 프로파일 ({stamp} UTC)",
        "",
        f"- 대상 project: `{pid}` · 청크 {n_chunks:,}건",
        f"- 질의 {len(queries)}건 × {REPEATS}회 (워밍업 1회 별도)",
        f"- 검색 결과: dense {info.get('dense')} · sparse {info.get('sparse')}"
        f" · 융합 {info.get('fused')}",
        "",
        "| 단계 | 평균 ms | 중앙값 ms | p95 ms | 전체 대비 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, mean, med, p95 in rows:
        share = f"{med / total:.0%}" if not name.startswith("5.") or name.endswith("50)") else "-"
        lines.append(f"| {name} | {mean:.1f} | {med:.1f} | {p95:.1f} | {share} |")
    lines += ["", f"**엔드투엔드 중앙값 합계: {total:.0f}ms**", "", "## 판정", ""]
    lines += _verdicts(timer, first_ms, later_ms, session_ids)
    lines += ["", "## 실행 환경", ""]
    lines += [f"- {k}: {v}" for k, v in _runtime_info().items()]
    lines.append(f"- 임베딩 첫 호출 {first_ms:.0f}ms → 이후 중앙값 {later_ms:.0f}ms")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines[6:]))
    print(f"\n[저장] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
