"""Manual NDCG@10 evaluator for the 4 retrieval modes.

Loads queries from ``eval/queries.yaml``, runs each through four search modes
(``semantic_only`` / ``keyword_only`` / ``hybrid`` / ``hybrid_rerank``), prompts
the user to grade each result 0~3 in a CLI loop, persists grades to
``eval/scores.json`` (resumable), and writes a markdown report to
``reports/search_eval_{timestamp}.md``.

The acceptance threshold (NDCG@10 > 0.6) is reported as *advisory* — the
tool emits a warning when below the threshold rather than failing. Formal
acceptance is a downstream policy decision based on the query pool and
labeling provenance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
import yaml

from src.clients.embedding_client import BgeM3Client
from src.clients.reranker_client import BgeRerankerV2M3Client
from src.core.config import settings
from src.core.logging import configure_logging
from src.db.session import async_session_maker
from src.services.retrieval import HybridSearchClient, rerank_hits
from src.services.retrieval._keyword import KeywordSearchClient
from src.services.retrieval._semantic import SemanticSearchClient

if TYPE_CHECKING:
    from src.services.retrieval.base import SearchHit, Track

logger = structlog.get_logger(__name__)

QUERIES_PATH = Path("./eval/queries.yaml")
SCORES_PATH = Path("./eval/scores.json")
REPORTS_DIR = Path("./reports")

MODES: tuple[str, ...] = ("semantic_only", "keyword_only", "hybrid", "hybrid_rerank")
K = 10
HYBRID_FANOUT_FOR_RERANK = 50

# Advisory 임계치 — 정식 합격선은 외부 라벨링 풀에서 별도 판정.
ADVISORY_NDCG = 0.6
# Sanity 최저선 — 이 아래면 인덱스·튜닝 등 시스템 본질 문제 의심.
SANITY_NDCG = 0.3

# 채점 prompt에서 ‘정답으로 간주’되는 최저 등급. Precision@10 계산에 사용.
PRECISION_RELEVANCE_THRESHOLD = 2


# ===========================================================================
# 지표 계산 — graded relevance용 표준 NDCG (2^r - 1 게인)
# ===========================================================================


def _dcg(grades: list[int]) -> float:
    """Discounted Cumulative Gain. 게인은 ``2^r - 1``, 분모는 ``log2(i + 2)``."""
    return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades))


def ndcg_at_k(grades: list[int], k: int = K) -> float:
    """Normalized DCG over the first ``k`` results.

    Returns 0.0 when the ideal DCG is also zero (모든 결과가 0점인 경우).
    """
    if not grades:
        return 0.0
    actual = _dcg(grades[:k])
    ideal = _dcg(sorted(grades[:k], reverse=True))
    return actual / ideal if ideal > 0 else 0.0


def precision_at_k(
    grades: list[int],
    k: int = K,
    threshold: int = PRECISION_RELEVANCE_THRESHOLD,
) -> float:
    """Fraction of the first ``k`` results graded at or above ``threshold``."""
    if not grades:
        return 0.0
    head = grades[:k]
    return sum(1 for g in head if g >= threshold) / len(head)


# ===========================================================================
# 데이터 모델
# ===========================================================================


@dataclass(frozen=True)
class QueryDef:
    id: str
    text: str
    category: str


@dataclass
class HitGrade:
    """저장 단위. chunk_id 키로 grading 결과를 보존."""

    chunk_id: str
    grade: int
    snippet: str  # 콘텐츠가 추후 변하더라도 채점 시 본 내용을 보존
    rated_at: str  # ISO timestamp


# ===========================================================================
# queries.yaml 로딩
# ===========================================================================


def load_queries(path: Path) -> list[QueryDef]:
    """YAML 파일을 ``QueryDef`` 리스트로 변환. 카테고리 미지정 시 'uncategorized'."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw or "queries" not in raw:
        raise ValueError(f"{path}: 'queries' 키 없음")

    out: list[QueryDef] = []
    seen_ids: set[str] = set()
    for entry in raw["queries"]:
        qid = entry["id"]
        if qid in seen_ids:
            raise ValueError(f"중복된 query id: {qid}")
        seen_ids.add(qid)
        out.append(
            QueryDef(
                id=qid,
                text=entry["text"],
                category=entry.get("category", "uncategorized"),
            )
        )
    if not out:
        raise ValueError(f"{path}: 평가 쿼리가 비어있음")
    return out


# ===========================================================================
# scores.json (resumable persistence)
# ===========================================================================


class ScoresStore:
    """``eval/scores.json``의 영구 저장소. set 한 번마다 atomic rename으로 저장."""

    def __init__(self, path: Path) -> None:
        self._path = path
        # 구조: dict[query_id, dict[mode, dict[chunk_id, HitGrade]]]
        self._data: dict[str, dict[str, dict[str, HitGrade]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        if path.exists():
            self._load()

    def _load(self) -> None:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        for qid, modes in raw.items():
            for mode, hits in modes.items():
                for chunk_id, payload in hits.items():
                    self._data[qid][mode][chunk_id] = HitGrade(**payload)

    def save(self) -> None:
        """원자적 저장 — temp 파일에 쓰고 rename. 중간 크래시에 안전."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {
            qid: {
                mode: {cid: hg.__dict__ for cid, hg in hits.items()} for mode, hits in modes.items()
            }
            for qid, modes in self._data.items()
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, query_id: str, mode: str, chunk_id: str) -> HitGrade | None:
        return self._data.get(query_id, {}).get(mode, {}).get(chunk_id)

    def set(self, query_id: str, mode: str, chunk_id: str, snippet: str, grade: int) -> None:
        self._data[query_id][mode][chunk_id] = HitGrade(
            chunk_id=chunk_id,
            grade=grade,
            snippet=snippet,
            rated_at=datetime.now(UTC).isoformat(),
        )

    def grades_for(self, query_id: str, mode: str, ordered_chunk_ids: list[str]) -> list[int]:
        """검색 결과 순서대로 grade 리스트 반환. 미채점은 0으로 채움."""
        per_mode = self._data.get(query_id, {}).get(mode, {})
        return [per_mode[cid].grade if cid in per_mode else 0 for cid in ordered_chunk_ids]

    def coverage(self, query_id: str, mode: str, ordered_chunk_ids: list[str]) -> tuple[int, int]:
        """채점된 hit 수와 전체 hit 수 반환 — 리포트의 coverage 컬럼용."""
        per_mode = self._data.get(query_id, {}).get(mode, {})
        rated = sum(1 for cid in ordered_chunk_ids if cid in per_mode)
        return rated, len(ordered_chunk_ids)


# ===========================================================================
# 검색 백엔드 wiring
# ===========================================================================


SearchModeFn = "Callable[[str], Awaitable[list[SearchHit]]]"


def build_search_modes(project_id: UUID, track: Track) -> dict[str, SearchModeFn]:
    """4개 모드의 검색 함수 dict 반환. 각 호출이 새 세션을 열어 race 없음."""
    embedder = BgeM3Client()
    semantic = SemanticSearchClient(async_session_maker, embedder)
    keyword = KeywordSearchClient(async_session_maker)
    hybrid = HybridSearchClient(semantic, keyword)
    reranker = BgeRerankerV2M3Client()

    async def semantic_only(query: str) -> list[SearchHit]:
        return await semantic.search(query, project_id, track, top_k=K)

    async def keyword_only(query: str) -> list[SearchHit]:
        return await keyword.search(query, project_id, track, top_k=K)

    async def hybrid_mode(query: str) -> list[SearchHit]:
        return await hybrid.search(query, project_id, track, top_k=K)

    async def hybrid_rerank(query: str) -> list[SearchHit]:
        # hybrid는 풍부한 후보를 뽑아주고 reranker가 상위 K개로 압축.
        candidates = await hybrid.search(query, project_id, track, top_k=HYBRID_FANOUT_FOR_RERANK)
        return await rerank_hits(reranker, query, candidates, top_k=K)

    return {
        "semantic_only": semantic_only,
        "keyword_only": keyword_only,
        "hybrid": hybrid_mode,
        "hybrid_rerank": hybrid_rerank,
    }


# ===========================================================================
# 검색 실행 + latency 측정
# ===========================================================================


@dataclass
class QueryResult:
    """(query, mode) 쌍의 검색 결과 + latency. 한 세션 동안 메모리에 보유."""

    query: QueryDef
    mode: str
    hits: list[SearchHit]
    latency_ms: float


async def run_all_searches(
    queries: list[QueryDef],
    search_modes: dict[str, SearchModeFn],
) -> list[QueryResult]:
    """모든 (query × mode) 검색을 미리 돌려놓는다. 80건 = 4초 내외."""
    results: list[QueryResult] = []
    for qd in queries:
        for mode_name, fn in search_modes.items():
            t0 = time.perf_counter()
            hits = await fn(qd.text)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            results.append(QueryResult(query=qd, mode=mode_name, hits=hits, latency_ms=latency_ms))
            logger.info(
                "eval.search_done",
                query_id=qd.id,
                mode=mode_name,
                hit_count=len(hits),
                latency_ms=round(latency_ms, 1),
            )
    return results


# ===========================================================================
# CLI 채점 UX
# ===========================================================================


def _display_hit(
    qd: QueryDef,
    mode: str,
    result_idx: int,
    result_total: int,
    query_pos: int,
    query_total: int,
    hit: SearchHit,
    current_grade: int | None,
) -> None:
    """한 hit 화면 출력. 이전 grade가 있으면 같이 보여준다."""
    bar = "─" * 70
    print(f"\n{bar}")
    print(f'[쿼리 {query_pos}/{query_total}] "{qd.text}"  (카테고리: {qd.category})')
    print(f"[모드: {mode}, 결과 {result_idx}/{result_total}]")
    print()
    snippet = hit.content[:600].rstrip()
    if len(hit.content) > 600:
        snippet += " …"
    print("청크 내용:")
    for line in snippet.splitlines() or [""]:
        print(f"  {line}")
    print()
    src_name = hit.metadata.get("source_name") if isinstance(hit.metadata, dict) else None
    print(
        f"출처: source_id={hit.source_id}, chunk_index={hit.chunk_index}"
        + (f", source_name={src_name}" if src_name else "")
    )
    print(f"내부 score={hit.score:.4f} (origin={hit.score_source}, chunk_id={hit.chunk_id})")
    if current_grade is not None:
        print(f"기존 채점: {current_grade}점 (덮어쓰려면 새 점수 입력, 유지하려면 s)")


def _prompt_grade(allow_back: bool) -> str:
    """0/1/2/3/s/q/b 입력 받기. 잘못된 입력은 재요청."""
    options = "0/1/2/3, s=skip, q=quit"
    if allow_back:
        options += ", b=back"
    while True:
        try:
            raw = input(f"점수 입력 ({options}): ").strip().lower()
        except EOFError:
            return "q"
        if raw in {"0", "1", "2", "3", "s", "q"}:
            return raw
        if raw == "b" and allow_back:
            return raw
        print("  → 유효 입력: 0, 1, 2, 3, s, q" + (", b" if allow_back else ""))


def interactive_grade(
    qresults: list[QueryResult],
    queries: list[QueryDef],
    store: ScoresStore,
) -> None:
    """전체 (query × mode × hit)을 평면화한 plan을 순회하며 채점."""
    plan: list[tuple[QueryResult, int]] = []  # (qresult, hit_idx)
    for qr in qresults:
        for hit_idx in range(len(qr.hits)):
            plan.append((qr, hit_idx))

    if not plan:
        print(
            "\n채점할 hit이 없습니다. queries.yaml의 쿼리가 인덱스된 청크와 매칭되는지 확인하세요."
        )
        return

    # 가장 먼저 채점되지 않은 위치에서 시작 — resume 동작.
    start_idx = next(
        (
            i
            for i, (qr, hidx) in enumerate(plan)
            if store.get(qr.query.id, qr.mode, str(qr.hits[hidx].chunk_id)) is None
        ),
        len(plan),
    )
    if start_idx == len(plan):
        print(f"\n이미 모든 hit이 채점됨 ({len(plan)}건). 채점 단계 건너뜀.")
        return

    query_total = len(queries)
    qid_to_pos = {qd.id: i + 1 for i, qd in enumerate(queries)}

    idx = start_idx
    while 0 <= idx < len(plan):
        qr, hit_idx = plan[idx]
        hit = qr.hits[hit_idx]
        chunk_id_str = str(hit.chunk_id)
        current = store.get(qr.query.id, qr.mode, chunk_id_str)
        _display_hit(
            qd=qr.query,
            mode=qr.mode,
            result_idx=hit_idx + 1,
            result_total=len(qr.hits),
            query_pos=qid_to_pos[qr.query.id],
            query_total=query_total,
            hit=hit,
            current_grade=current.grade if current else None,
        )
        cmd = _prompt_grade(allow_back=idx > 0)
        if cmd == "q":
            print("\n중간 종료 — 다음 실행 시 이어서 채점됩니다.")
            return
        if cmd == "b":
            idx -= 1
            continue
        if cmd == "s":
            idx += 1
            continue
        # 0/1/2/3
        store.set(
            qr.query.id,
            qr.mode,
            chunk_id_str,
            snippet=hit.content[:500],
            grade=int(cmd),
        )
        store.save()
        idx += 1

    print("\n전체 채점 완료 ✓")


# ===========================================================================
# 리포트 생성
# ===========================================================================


@dataclass
class ModeMetrics:
    mode: str
    avg_ndcg: float
    avg_p_at_k: float
    avg_latency_ms: float
    rated_total: int
    hits_total: int


@dataclass
class CategoryBreakdown:
    category: str
    per_mode_ndcg: dict[str, float]


def _compute_mode_metrics(
    qresults: list[QueryResult], store: ScoresStore
) -> dict[str, ModeMetrics]:
    by_mode: dict[str, list[QueryResult]] = defaultdict(list)
    for qr in qresults:
        by_mode[qr.mode].append(qr)

    out: dict[str, ModeMetrics] = {}
    for mode in MODES:
        rows = by_mode.get(mode, [])
        if not rows:
            out[mode] = ModeMetrics(mode, 0.0, 0.0, 0.0, 0, 0)
            continue
        ndcg_vals: list[float] = []
        p_vals: list[float] = []
        latency_vals: list[float] = []
        rated_total = 0
        hits_total = 0
        for qr in rows:
            chunk_ids = [str(h.chunk_id) for h in qr.hits]
            grades = store.grades_for(qr.query.id, qr.mode, chunk_ids)
            ndcg_vals.append(ndcg_at_k(grades))
            p_vals.append(precision_at_k(grades))
            latency_vals.append(qr.latency_ms)
            rated, total = store.coverage(qr.query.id, qr.mode, chunk_ids)
            rated_total += rated
            hits_total += total
        out[mode] = ModeMetrics(
            mode=mode,
            avg_ndcg=sum(ndcg_vals) / len(ndcg_vals),
            avg_p_at_k=sum(p_vals) / len(p_vals),
            avg_latency_ms=sum(latency_vals) / len(latency_vals),
            rated_total=rated_total,
            hits_total=hits_total,
        )
    return out


def _compute_category_breakdown(
    qresults: list[QueryResult], store: ScoresStore
) -> list[CategoryBreakdown]:
    by_cat_mode: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for qr in qresults:
        chunk_ids = [str(h.chunk_id) for h in qr.hits]
        grades = store.grades_for(qr.query.id, qr.mode, chunk_ids)
        by_cat_mode[qr.query.category][qr.mode].append(ndcg_at_k(grades))

    out: list[CategoryBreakdown] = []
    for cat in sorted(by_cat_mode):
        per_mode = {
            mode: (
                sum(by_cat_mode[cat][mode]) / len(by_cat_mode[cat][mode])
                if by_cat_mode[cat].get(mode)
                else 0.0
            )
            for mode in MODES
        }
        out.append(CategoryBreakdown(category=cat, per_mode_ndcg=per_mode))
    return out


def _verdict(mode_metrics: dict[str, ModeMetrics]) -> list[str]:
    """advisory 임계치 평가 결과 메시지 리스트."""
    msgs: list[str] = []
    hr = mode_metrics.get("hybrid_rerank")
    semantic = mode_metrics.get("semantic_only")

    if hr is None:
        return ["hybrid_rerank 결과 없음 — 평가 불가."]

    if hr.avg_ndcg >= ADVISORY_NDCG:
        msgs.append(
            f"✓ hybrid_rerank NDCG@10 = {hr.avg_ndcg:.3f} ≥ {ADVISORY_NDCG} (advisory 통과)."
        )
    elif hr.avg_ndcg >= SANITY_NDCG:
        msgs.append(
            f"⚠ hybrid_rerank NDCG@10 = {hr.avg_ndcg:.3f} ∈ [{SANITY_NDCG}, {ADVISORY_NDCG}) — "
            "정식 합격선(0.6) 미달. 확장된 평가셋에서 재판정 필요."
        )
    else:
        msgs.append(
            f"✗ hybrid_rerank NDCG@10 = {hr.avg_ndcg:.3f} < {SANITY_NDCG} (sanity 미달). "
            "시스템 본질 문제 가능성 — 인덱스·튜닝 보강 검토."
        )

    if semantic and hr.avg_ndcg >= semantic.avg_ndcg * 1.10:
        diff = (hr.avg_ndcg - semantic.avg_ndcg) / semantic.avg_ndcg * 100
        msgs.append(
            f"✓ hybrid_rerank가 semantic_only 대비 +{diff:.1f}% (≥ +10%, fusion 효과 확인)."
        )
    elif semantic and semantic.avg_ndcg > 0:
        diff = (hr.avg_ndcg - semantic.avg_ndcg) / semantic.avg_ndcg * 100
        msgs.append(
            f"⚠ hybrid_rerank가 semantic_only 대비 {diff:+.1f}% — fusion 효과 부족. "
            "RRF k·fan-out·reranker 설정 점검 검토."
        )
    return msgs


def write_report(
    *,
    report_path: Path,
    queries: list[QueryDef],
    qresults: list[QueryResult],
    store: ScoresStore,
    project_id: UUID,
    track: Track,
) -> dict[str, ModeMetrics]:
    """리포트를 마크다운으로 생성. 호출자는 mode_metrics를 받아 후처리 가능."""
    mode_metrics = _compute_mode_metrics(qresults, store)
    cat_breakdown = _compute_category_breakdown(qresults, store)

    now = datetime.now(UTC).isoformat()
    lines: list[str] = []
    lines.append("# 검색 평가 리포트 (수동 NDCG@10)")
    lines.append("")
    lines.append(f"생성 시각: {now}")
    lines.append(f"프로젝트: `{project_id}` · 트랙: `{track}`")
    lines.append(f"쿼리 수: {len(queries)} · 모드 수: {len(MODES)} · top-K: {K}")
    lines.append("")
    lines.append("> Advisory 임계치")
    lines.append(f"> - 정식 합격선: NDCG@10 > {ADVISORY_NDCG}")
    lines.append(f"> - sanity 최저선: NDCG@10 > {SANITY_NDCG}")
    lines.append("> 본 리포트의 수치는 advisory — 정식 합격 판정은 확장된 라벨링 풀에서 진행한다.")
    lines.append("")
    lines.append("## 1. 모드 비교")
    lines.append("")
    lines.append("| 모드 | NDCG@10 | P@10 (≥2점) | 평균 응답시간 (ms) | 채점 커버리지 |")
    lines.append("|---|---|---|---|---|")
    for mode in MODES:
        m = mode_metrics[mode]
        coverage = f"{m.rated_total}/{m.hits_total}" if m.hits_total else "n/a"
        lines.append(
            f"| `{mode}` | {m.avg_ndcg:.3f} | {m.avg_p_at_k:.3f} | "
            f"{m.avg_latency_ms:.1f} | {coverage} |"
        )

    lines.append("")
    lines.append("## 2. 카테고리별 NDCG@10")
    lines.append("")
    header = "| 카테고리 | " + " | ".join(f"`{m}`" for m in MODES) + " |"
    sep = "|---|" + "---|" * len(MODES)
    lines.append(header)
    lines.append(sep)
    for cb in cat_breakdown:
        cells = " | ".join(f"{cb.per_mode_ndcg[m]:.3f}" for m in MODES)
        lines.append(f"| {cb.category} | {cells} |")

    lines.append("")
    lines.append("## 3. 임계치 평가 (advisory)")
    lines.append("")
    for msg in _verdict(mode_metrics):
        lines.append(f"- {msg}")

    lines.append("")
    lines.append("## 4. 후속 안내")
    lines.append("")
    hr = mode_metrics.get("hybrid_rerank")
    if hr and hr.avg_ndcg < ADVISORY_NDCG:
        lines.append(
            "- 정식 합격선(0.6) 미달. 본 리포트를 시연 자료로 활용 + 라벨링 협의 후 재평가."
        )
        lines.append(
            "- 대안 검토: (a) RRF k 조정, (b) reranker batch/max_length 튜닝, "
            "(c) Mecab 토크나이저 도입, (d) 인덱싱 보강."
        )
    else:
        lines.append("- advisory 통과. 확장된 라벨링 풀에서 재측정해 정식 합격 판정 진행.")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mode_metrics


# ===========================================================================
# CLI 진입점
# ===========================================================================


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Manual NDCG@10 evaluator for 4 retrieval modes")
    p.add_argument("--project-id", required=True, type=UUID, help="평가 대상 project UUID")
    p.add_argument(
        "--track", default="content", choices=("content", "style"), help="검색 트랙 (기본 content)"
    )
    p.add_argument("--queries", default=str(QUERIES_PATH), type=Path, help="평가 쿼리 YAML 경로")
    p.add_argument(
        "--scores", default=str(SCORES_PATH), type=Path, help="채점 결과 JSON 경로 (resumable)"
    )
    p.add_argument(
        "--report-only",
        action="store_true",
        help="채점 단계 건너뛰고 기존 scores.json만으로 리포트 생성",
    )
    return p.parse_args(argv)


async def amain(args: argparse.Namespace) -> int:
    configure_logging()
    if not settings.reranker_enabled:
        print(
            "WARN: settings.reranker_enabled=False — 어댑터는 띄우지만 실측은 수행.",
            file=sys.stderr,
        )

    queries = load_queries(args.queries)
    store = ScoresStore(args.scores)
    search_modes = build_search_modes(args.project_id, args.track)

    print(f"\n=== 검색 평가 시작 — 쿼리 {len(queries)}개 × 모드 {len(MODES)}개 ===\n")
    print("Step 1: 4개 모드 검색 미리 실행 (latency 측정 포함)…")
    qresults = await run_all_searches(queries, search_modes)
    hit_total = sum(len(qr.hits) for qr in qresults)
    print(f"Step 1 완료: 총 hit {hit_total}건")

    if not args.report_only:
        print("\nStep 2: 수동 채점 단계 — 0/1/2/3 입력, s=skip, q=quit, b=back\n")
        interactive_grade(qresults, queries, store)

    print("\nStep 3: 리포트 생성")
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"search_eval_{ts}.md"
    mode_metrics = write_report(
        report_path=report_path,
        queries=queries,
        qresults=qresults,
        store=store,
        project_id=args.project_id,
        track=args.track,
    )
    print(f"  → {report_path}")

    hr = mode_metrics.get("hybrid_rerank")
    if hr:
        print(f"\nhybrid_rerank NDCG@10 = {hr.avg_ndcg:.3f}", end="")
        if hr.avg_ndcg >= ADVISORY_NDCG:
            print(f" (≥ {ADVISORY_NDCG} advisory 통과 ✓)")
        elif hr.avg_ndcg >= SANITY_NDCG:
            print(f" (sanity {SANITY_NDCG} 통과, advisory {ADVISORY_NDCG} 미달 ⚠)")
        else:
            print(f" (sanity {SANITY_NDCG} 미달 ✗ — 시스템 점검 필요)")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
