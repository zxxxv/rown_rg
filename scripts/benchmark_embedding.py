"""Embedding benchmark on real company reports.

Measures BGE-M3 INT8 embedding speed: per-paragraph mean/median/p95 ms,
batch-size trade-off, cold/warm cache effect, peak RSS, and a 500-page
projection.

Usage:
    uv run python scripts/benchmark_embedding.py
        [--max-paragraphs N]  # truncate per file for faster runs
        [--sample-size N]     # batch-size sweep sample size (default 200)
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import resource
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import psutil
import structlog

from src.clients.embedding_client import BgeM3Client, EmbeddingCache
from src.core.logging import configure_logging

logger = structlog.get_logger(__name__)

PARSED_DIR = Path("./test_data/parsed")
REPORT_PATH = Path("./reports/embedding_benchmark.md")
TMP_CACHE_ROOT = Path("./cache/_embedding_benchmark")

# 합격 기준 네 가지 — mean·p95 단락당 ms, 500p 보고서 견적 분, peak RAM GB.
MAX_MEAN_MS = 300.0
MAX_P95_MS = 500.0
MAX_500P_MINUTES = 30.0
MAX_PEAK_RAM_GB = 4.0

# 500p 보고서 견적용 추정치 — 회사 보고서 표본에서 페이지당 ~6단락 관측.
PARAGRAPHS_PER_PAGE = 6
ESTIMATED_PAGES = 500
TOTAL_PARAGRAPHS_FOR_ESTIMATE = PARAGRAPHS_PER_PAGE * ESTIMATED_PAGES

DEFAULT_SAMPLE_SIZE = 200

# sweep 점은 클라이언트 자동값에 대한 배수. 0.5x·1x로 자동값 아래·자동값 두 점을 측정해
# "자동값에서 줄였을 때 손해/이득" 곡선의 방향을 호스트 사양과 무관하게 일관되게 본다.
# 자동값 위는 클라이언트 hard cap이 거절하므로 별도 큰 호스트에서 측정.
SWEEP_MULTIPLIERS = (0.5, 1.0)
# 외부 chunk loop의 RSS hard cutoff = 시스템 총 RAM의 이 비율. 임계 도달 시 즉시 raise —
# 부드럽게 끝내지 않고 명시적으로 실패시켜 같은 부하 패턴을 재시도하기 전 가드를 점검하게
# 만든다. 비율로 두면 큰 호스트에서 자동으로 더 넉넉한 cutoff를 가진다.
RSS_HARD_LIMIT_RATIO = 0.4
# 프로세스 가상 메모리 상한 = 시스템 총 RAM의 이 비율. swap thrash로 OS hard freeze가
# 일어나기 전 MemoryError로 먼저 끝낸다. baseline 모델·텐서·캐시·numpy 버퍼를 합쳐
# 충분한 마진이 필요해 너무 빡빡한 값은 정상 측정도 죽일 수 있다.
VIRTUAL_MEM_LIMIT_RATIO = 0.6

# 호스트 RAM과 클라이언트 자동값은 부팅 사이에 안 바뀌므로 모듈 import 시 1회 결정해 박는다.
# 측정/sweep/watchdog/apply_memory_limits가 모두 같은 상수를 본다.
_HOST_TOTAL_RAM_BYTES: int = psutil.virtual_memory().total
_HOST_TOTAL_RAM_GB: float = _HOST_TOTAL_RAM_BYTES / 1024**3
_AUTO_MAX_CHARS: int = BgeM3Client.auto_max_chars_per_batch()
MAX_CHARS_SWEEP: tuple[int, ...] = tuple(int(_AUTO_MAX_CHARS * m) for m in SWEEP_MULTIPLIERS)
RSS_HARD_LIMIT_GB: float = _HOST_TOTAL_RAM_GB * RSS_HARD_LIMIT_RATIO


def split_paragraphs(markdown: str) -> list[str]:
    """Split markdown into non-empty paragraphs by blank lines.

    ``\\n\\n`` 단순 분리. 의미 단위 청킹은 별도 단계에서 다루며, 여기서는
    "임베딩 호출 단위로 의미 있는 블록 하나"를 잡기 위한 단순화.
    """
    blocks = (b.strip() for b in markdown.split("\n\n"))
    return [b for b in blocks if b]


def compute_stats_ms(times_ms: Iterable[float]) -> dict[str, float]:
    """Return mean/median/p95 (linear interpolation) of a ms sample."""
    arr = np.asarray(list(times_ms), dtype=np.float64)
    if arr.size == 0:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0}
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p95": float(np.percentile(arr, 95)),
    }


def estimate_500p_minutes(mean_ms_per_para: float) -> float:
    """Project total time to embed a 500-page report at ``mean_ms_per_para``."""
    total_seconds = (mean_ms_per_para / 1000.0) * TOTAL_PARAGRAPHS_FOR_ESTIMATE
    return total_seconds / 60.0


def bytes_to_gb(b: int) -> float:
    return b / (1024**3)


def apply_memory_limits() -> None:
    """Cap virtual memory at a fraction of host RAM to fail fast before swap thrash.

    The benchmark process is killed by the OS once the system starts thrashing
    swap. Setting ``RLIMIT_AS`` lets Python raise ``MemoryError`` first, which
    is recoverable and gives a stack trace pointing at the offending batch.
    """
    # 사용자 셸이 이미 더 엄격한 ulimit -v를 걸어 둔 경우 그 한도를 깨지 않도록 hard는 유지.
    desired = int(_HOST_TOTAL_RAM_BYTES * VIRTUAL_MEM_LIMIT_RATIO)
    try:
        _, current_hard = resource.getrlimit(resource.RLIMIT_AS)
        if current_hard != resource.RLIM_INFINITY and desired > current_hard:
            new_soft = current_hard
        else:
            new_soft = desired
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, current_hard))
        print(f"[safety] RLIMIT_AS soft={bytes_to_gb(new_soft):.1f}GB")
    except (ValueError, OSError) as e:
        # 컨테이너·일부 호스트에서 setrlimit이 거부될 수 있음. 셸 측 ulimit -v로 대체 가능.
        print(f"[safety] RLIMIT_AS not applied ({type(e).__name__}: {e})")


def check_rss_or_abort(proc: psutil.Process, chunk_idx: int, total_chunks: int) -> int:
    """Return current RSS; raise if it exceeds ``RSS_HARD_LIMIT_GB``."""
    rss = proc.memory_info().rss
    if bytes_to_gb(rss) > RSS_HARD_LIMIT_GB:
        raise RuntimeError(
            f"RSS {bytes_to_gb(rss):.2f}GB exceeded hard cutoff "
            f"{RSS_HARD_LIMIT_GB:.1f}GB at chunk {chunk_idx}/{total_chunks}. "
            f"Aborting before OOM kill."
        )
    return rss


# 외부 timing chunk 크기. EmbeddingClient가 내부적으로 정렬·동적 배치를 하지만 per-paragraph
# 분포(median, p95)를 얻으려면 외부에서 적당히 잘라 측정해야 한다. 너무 크면 chunk 안에서
# 분포가 평탄화되고, 너무 작으면 padding 회복 효과가 줄어든다 — 64 정도가 무난.
TIMING_CHUNK_SIZE = 64


def embed_with_timings(
    client: BgeM3Client,
    paragraphs: list[str],
) -> tuple[list[float], float, float]:
    """Time embed_batch per chunk for distribution; return per-paragraph ms,
    total seconds, peak RSS GB.

    EmbeddingClient.embed_batch이 내부적으로 글자 수 정렬 + 누적 글자 수 기반 동적
    배치를 수행하므로 호출자는 chunk를 그대로 넘기기만 하면 padding overhead 회피와
    OOM 안전이 자동 적용된다. 외부 chunk 분할은 오직 per-paragraph timing 분포
    (median/p95)를 얻기 위한 timing granularity 용도.
    """
    proc = psutil.Process()
    peak_rss = proc.memory_info().rss
    t0 = time.perf_counter()

    total_chunks = (len(paragraphs) + TIMING_CHUNK_SIZE - 1) // TIMING_CHUNK_SIZE
    per_para_ms: list[float] = []
    for idx, start in enumerate(range(0, len(paragraphs), TIMING_CHUNK_SIZE), start=1):
        chunk = paragraphs[start : start + TIMING_CHUNK_SIZE]
        # 외부 watchdog — 클라이언트 내부 MEMORY_PRESSURE_THRESHOLD가 GC로도 못 끄는
        # 누적 RSS를 hard cutoff로 막는다. 다음 chunk forward 직전 체크.
        check_rss_or_abort(proc, idx, total_chunks)
        c0 = time.perf_counter()
        asyncio.run(client.embed_batch(chunk))
        chunk_ms = (time.perf_counter() - c0) * 1000.0
        per_para_ms.extend([chunk_ms / len(chunk)] * len(chunk))
        peak_rss = max(peak_rss, proc.memory_info().rss)

    total_s = time.perf_counter() - t0
    return per_para_ms, total_s, bytes_to_gb(peak_rss)


def measure_cold(
    paragraphs: list[str],
    cache_root: Path,
    max_chars_per_batch: int | None = None,
) -> dict[str, float | int]:
    """Cold measurement: fresh cache, fresh client."""
    cache_root.mkdir(parents=True, exist_ok=True)
    client = BgeM3Client(
        cache=EmbeddingCache(root=cache_root),
        max_chars_per_batch=max_chars_per_batch,
    )
    try:
        times, total_s, peak_gb = embed_with_timings(client, paragraphs)
        stats = compute_stats_ms(times)
        return {
            "paragraphs": len(paragraphs),
            "max_chars_per_batch": client._max_chars_per_batch,
            "mean_ms": stats["mean"],
            "median_ms": stats["median"],
            "p95_ms": stats["p95"],
            "total_s": total_s,
            "peak_ram_gb": peak_gb,
        }
    finally:
        # 다음 measure_*가 새 InferenceSession을 로드하기 전 native heap을 비운다.
        # Python GC는 ONNX runtime 텐서를 즉시 풀지 못해 sweep 누적으로 OOM 가능.
        del client
        gc.collect()


def measure_warm(
    paragraphs: list[str],
    cache_root: Path,
    max_chars_per_batch: int | None = None,
) -> dict[str, float | int]:
    """Warm measurement: same cache, second pass — should be ~all cache hits."""
    client = BgeM3Client(
        cache=EmbeddingCache(root=cache_root),
        max_chars_per_batch=max_chars_per_batch,
    )
    try:
        times, total_s, peak_gb = embed_with_timings(client, paragraphs)
        stats = compute_stats_ms(times)
        return {
            "paragraphs": len(paragraphs),
            "max_chars_per_batch": client._max_chars_per_batch,
            "mean_ms": stats["mean"],
            "median_ms": stats["median"],
            "p95_ms": stats["p95"],
            "total_s": total_s,
            "peak_ram_gb": peak_gb,
        }
    finally:
        del client
        gc.collect()


def evaluate_thresholds(
    mean_ms: float, p95_ms: float, peak_ram_gb: float
) -> tuple[bool, list[str]]:
    """Return (ok, failures) for the four [4] checklist criteria."""
    failures: list[str] = []
    if mean_ms > MAX_MEAN_MS:
        failures.append(f"mean_ms={mean_ms:.1f} > {MAX_MEAN_MS:.0f}")
    if p95_ms > MAX_P95_MS:
        failures.append(f"p95_ms={p95_ms:.1f} > {MAX_P95_MS:.0f}")
    if estimate_500p_minutes(mean_ms) > MAX_500P_MINUTES:
        failures.append(
            f"500p_minutes={estimate_500p_minutes(mean_ms):.1f} > {MAX_500P_MINUTES:.0f}"
        )
    if peak_ram_gb > MAX_PEAK_RAM_GB:
        failures.append(f"peak_ram_gb={peak_ram_gb:.2f} > {MAX_PEAK_RAM_GB:.1f}")
    return (not failures, failures)


def _fmt_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def render_main_table(rows: list[dict[str, float | int | str]]) -> str:
    headers = [
        "보고서",
        "단락수",
        "평균ms",
        "중앙값ms",
        "p95ms",
        "총시간(s)",
        "Peak RAM(GB)",
        "500p견적(min)",
    ]
    sep = ["---"] * len(headers)
    lines = [_fmt_row(headers), _fmt_row(sep)]
    for r in rows:
        mean_ms = float(r["mean_ms"])
        lines.append(
            _fmt_row(
                [
                    str(r["file"]),
                    f"{int(r['paragraphs'])}",
                    f"{mean_ms:.1f}",
                    f"{float(r['median_ms']):.1f}",
                    f"{float(r['p95_ms']):.1f}",
                    f"{float(r['total_s']):.1f}",
                    f"{float(r['peak_ram_gb']):.2f}",
                    f"{estimate_500p_minutes(mean_ms):.1f}",
                ]
            )
        )
    return "\n".join(lines)


def render_batch_table(rows: list[dict[str, float | int]]) -> str:
    headers = [
        "max_chars_per_batch",
        "단락수",
        "평균ms",
        "중앙값ms",
        "p95ms",
        "총시간(s)",
        "Peak RAM(GB)",
    ]
    sep = ["---"] * len(headers)
    lines = [_fmt_row(headers), _fmt_row(sep)]
    for r in rows:
        lines.append(
            _fmt_row(
                [
                    f"{int(r['max_chars_per_batch'])}",
                    f"{int(r['paragraphs'])}",
                    f"{float(r['mean_ms']):.1f}",
                    f"{float(r['median_ms']):.1f}",
                    f"{float(r['p95_ms']):.1f}",
                    f"{float(r['total_s']):.1f}",
                    f"{float(r['peak_ram_gb']):.2f}",
                ]
            )
        )
    return "\n".join(lines)


def render_warm_table(cold: dict, warm: dict) -> str:
    headers = ["케이스", "평균ms", "중앙값ms", "p95ms", "총시간(s)"]
    sep = ["---"] * len(headers)
    lines = [_fmt_row(headers), _fmt_row(sep)]
    for label, r in (("cold (캐시 미스)", cold), ("warm (캐시 적중)", warm)):
        lines.append(
            _fmt_row(
                [
                    label,
                    f"{float(r['mean_ms']):.2f}",
                    f"{float(r['median_ms']):.2f}",
                    f"{float(r['p95_ms']):.2f}",
                    f"{float(r['total_s']):.2f}",
                ]
            )
        )
    return "\n".join(lines)


def write_report(
    *,
    main_rows: list[dict],
    batch_rows: list[dict],
    cold_warm: tuple[dict, dict] | None,
    verdict_ok: bool,
    failures: list[str],
    sample_size: int,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cpu_count = psutil.cpu_count(logical=True)
    ram_gb = bytes_to_gb(psutil.virtual_memory().total)

    sections: list[str] = []
    sections.append(
        f"# Embedding Benchmark\n\n"
        f"- 생성: `{datetime.now(UTC).isoformat()}`\n"
        f"- BGE-M3 ONNX INT8 (`models/bge-m3-onnx-int8/`)\n"
        f"- 환경: CPU {cpu_count} cores, RAM {ram_gb:.1f} GB\n"
        f"- 라이브러리: sentence-transformers {version('sentence-transformers')}, "
        f"onnxruntime {version('onnxruntime')}, transformers {version('transformers')}\n"
        f"- Batching: length-aware (단락을 글자 수로 정렬 후 batch 묶음 — "
        f"같은 batch 내 시퀀스 길이가 균일해져 padding overhead 제거)\n"
    )

    sections.append(
        "## 1. 자료별 전체 인덱싱 (RAM-auto max_chars_per_batch, cold)\n\n"
        + render_main_table(main_rows)
    )

    sections.append(
        f"## 2. max_chars_per_batch 비교 (sample {sample_size}단락, cold)\n\n"
        + render_batch_table(batch_rows)
        + "\n\n동적 배치 상한이 단락당 ms·peak RAM에 미치는 영향. 16GB RAM에서 자동값은 "
        "32_000자. 더 크게 잡으면 padding overhead가 누적돼 평균 시간이 증가하거나 RAM이 "
        "치솟고 (OOM 위험), 더 작게 잡으면 forward pass 횟수가 늘어 throughput이 떨어진다."
    )

    if cold_warm is not None:
        cold, warm = cold_warm
        sections.append(
            f"## 3. 캐시 효과 (sample {sample_size}단락)\n\n"
            + render_warm_table(cold, warm)
            + "\n\n같은 단락을 다시 임베딩하면 디스크 캐시에서 직접 로드되어 ONNX 호출이 "
            "0회. warm 평균 ms가 cold의 일부에 그치면 캐시가 의도대로 동작."
        )

    pass_table = "\n".join(
        [
            _fmt_row(["기준", "목표", "실측 (대표 케이스)", "통과"]),
            _fmt_row(["---"] * 4),
        ]
    )
    rep = main_rows[0] if main_rows else None
    if rep is not None:
        rep_mean = float(rep["mean_ms"])
        rep_p95 = float(rep["p95_ms"])
        rep_ram = float(rep["peak_ram_gb"])
        rep_500p = estimate_500p_minutes(rep_mean)
        pass_table += "\n" + "\n".join(
            [
                _fmt_row(
                    [
                        "단락당 평균",
                        f"< {MAX_MEAN_MS:.0f}ms",
                        f"{rep_mean:.1f}ms",
                        "✓" if rep_mean <= MAX_MEAN_MS else "✗",
                    ]
                ),
                _fmt_row(
                    [
                        "단락당 p95",
                        f"< {MAX_P95_MS:.0f}ms",
                        f"{rep_p95:.1f}ms",
                        "✓" if rep_p95 <= MAX_P95_MS else "✗",
                    ]
                ),
                _fmt_row(
                    [
                        "500p 견적",
                        f"< {MAX_500P_MINUTES:.0f}분",
                        f"{rep_500p:.1f}분",
                        "✓" if rep_500p <= MAX_500P_MINUTES else "✗",
                    ]
                ),
                _fmt_row(
                    [
                        "Peak RAM",
                        f"< {MAX_PEAK_RAM_GB:.1f}GB",
                        f"{rep_ram:.2f}GB",
                        "✓" if rep_ram <= MAX_PEAK_RAM_GB else "✗",
                    ]
                ),
            ]
        )

    if verdict_ok:
        verdict = "**판정: PASS** ✓ — 합격 기준 4종 모두 통과."
    else:
        verdict = "**판정: FAIL** ✗\n\n실패 항목:\n" + "\n".join(f"- `{f}`" for f in failures)
        verdict += (
            "\n\n**대안 모델 검토**: 한국어 품질 기준 미달 시 다른 다국어 임베딩 백엔드 "
            "(예: Qwen3-Embedding, EmbeddingGemma 등) 추가 평가 권장."
        )

    sections.append("## 4. 합격 기준 평가\n\n" + pass_table + "\n\n" + verdict)
    REPORT_PATH.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BGE-M3 embedding benchmark")
    p.add_argument(
        "--max-paragraphs",
        type=int,
        default=None,
        help="자료당 임베딩 단락 최대치 (cold 측정 시간 단축용). 미지정 시 전체.",
    )
    p.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"배치 비교·cold/warm 측정 sample 크기 (기본 {DEFAULT_SAMPLE_SIZE})",
    )
    p.add_argument(
        "--max-paragraph-chars",
        type=int,
        default=None,
        help=(
            "단락 최대 글자 수. 초과 단락을 측정에서 제외 — length-aware batching이 "
            "padding 왜곡을 이미 제거하므로 보통은 불필요. 청킹 후 분포로 비교 측정"
            "하고 싶을 때만 사용."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    apply_memory_limits()

    md_files = sorted(PARSED_DIR.glob("*.md"))
    if not md_files:
        print(f"[ERROR] {PARSED_DIR} 에 .md 파일이 없습니다.")
        return 2

    print(f"=== 측정 자료: {len(md_files)}건 ===")
    for f in md_files:
        print(f"  {f.name}  ({f.stat().st_size / 1024:.1f} KB)")
    print()

    logger.info("embedding_benchmark.started", n_files=len(md_files))

    # === Stage 1: 자료별 cold (RAM-auto max_chars_per_batch) ===
    main_rows: list[dict] = []
    rep_paragraphs: list[str] | None = None
    for md_path in md_files:
        text = md_path.read_text(encoding="utf-8")
        paragraphs = split_paragraphs(text)
        if args.max_paragraph_chars is not None:
            before = len(paragraphs)
            paragraphs = [p for p in paragraphs if len(p) <= args.max_paragraph_chars]
            print(
                f"  단락 길이 필터: {before} → {len(paragraphs)} "
                f"(max {args.max_paragraph_chars}자, 청킹 가정)"
            )
        if args.max_paragraphs is not None:
            paragraphs = paragraphs[: args.max_paragraphs]
        print(f"[1] {md_path.name}: {len(paragraphs)}단락 cold (RAM-auto)")
        cache = TMP_CACHE_ROOT / md_path.stem / "cold_main"
        if cache.exists():
            import shutil

            shutil.rmtree(cache)
        res = measure_cold(paragraphs, cache)
        res["file"] = md_path.name
        main_rows.append(res)
        if rep_paragraphs is None:
            rep_paragraphs = paragraphs

    # === Stage 2: max_chars_per_batch 비교 (sample only) ===
    sample = (rep_paragraphs or [])[: args.sample_size]
    batch_rows: list[dict] = []
    if sample:
        print(f"\n[2] max_chars_per_batch 비교: sample {len(sample)}단락")
        for max_chars in MAX_CHARS_SWEEP:
            cache = TMP_CACHE_ROOT / "_max_chars_sweep" / f"mc{max_chars}"
            if cache.exists():
                import shutil

                shutil.rmtree(cache)
            res = measure_cold(sample, cache, max_chars_per_batch=max_chars)
            batch_rows.append(res)
            print(
                f"   max_chars={max_chars}: mean={res['mean_ms']:.1f}ms "
                f"p95={res['p95_ms']:.1f}ms total={res['total_s']:.1f}s "
                f"RAM={res['peak_ram_gb']:.2f}GB"
            )

    # === 스테이지 3: cold vs warm (샘플) ===
    cold_warm: tuple[dict, dict] | None = None
    if sample:
        print("\n[3] cold vs warm 캐시 효과")
        cache_cw = TMP_CACHE_ROOT / "_warm_test"
        if cache_cw.exists():
            import shutil

            shutil.rmtree(cache_cw)
        cold = measure_cold(sample, cache_cw)
        warm = measure_warm(sample, cache_cw)
        cold_warm = (cold, warm)
        print(f"   cold: mean={cold['mean_ms']:.1f}ms total={cold['total_s']:.1f}s")
        print(f"   warm: mean={warm['mean_ms']:.2f}ms total={warm['total_s']:.2f}s")

    # === 합격 판정 (대표: 첫 자료의 cold main) ===
    rep = main_rows[0]
    verdict_ok, failures = evaluate_thresholds(
        mean_ms=float(rep["mean_ms"]),
        p95_ms=float(rep["p95_ms"]),
        peak_ram_gb=float(rep["peak_ram_gb"]),
    )

    write_report(
        main_rows=main_rows,
        batch_rows=batch_rows,
        cold_warm=cold_warm,
        verdict_ok=verdict_ok,
        failures=failures,
        sample_size=args.sample_size,
    )
    logger.info("embedding_benchmark.report.written", path=str(REPORT_PATH))

    if not verdict_ok:
        print(f"\n[FAIL] 합격 기준 미달: {failures}")
        print(f"리포트: {REPORT_PATH}")
        return 1

    print(f"\n[PASS] 합격 기준 4종 모두 통과. 리포트: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
