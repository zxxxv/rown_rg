"""BGE Reranker v2-m3 ONNX 변환 + INT8 동적 양자화 셋업 CLI.

사용법:
    uv run python scripts/setup_bge_reranker.py [--force] [--benchmark-pairs 50] [--pre-check]

--pre-check: 코드 작성 전 가정 검증용 인스펙션 출력만 하고 종료.
--force:     캐시된 fp32/int8 산출물을 무시하고 다시 변환.

setup_bge_m3.py와 동일 패턴 — task만 ``text-classification``으로 다르고, 정확도는
PyTorch logit vs ONNX logit의 절대 차이로 측정 (cross-encoder는 단일 logit 출력).
"""

from __future__ import annotations

import argparse
import inspect
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import structlog

from src.core.logging import configure_logging

logger = structlog.get_logger(__name__)

MODEL_ID = "BAAI/bge-reranker-v2-m3"
FP32_DIR = Path("./models/bge-reranker-v2-m3-onnx-fp32")
INT8_DIR = Path("./models/bge-reranker-v2-m3-onnx-int8")
MODEL_FILE = "model.onnx"
REPORT_PATH = Path("./reports/bge_reranker_setup.md")
TOKENIZER_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
    "sentencepiece.bpe.model",
)

# 합격 기준
MAX_SIZE_MB = 700.0
MAX_ACCURACY_LOSS = 0.05
MAX_50PAIR_SEC = 1.0

# 평가 데이터 — (query, [관련 passage, 무관 passage]) 한국어 쌍
KOREAN_PAIRS: list[tuple[str, str]] = [
    ("노인 일자리 정책", "정부의 고령자 일자리 사업 확대 방안이 발표됐다."),
    ("노인 일자리 정책", "조선 시대 도자기 제작 기법에 대한 연구."),
    ("SOC 사업 경제성", "SOC 투자의 B/C 비율은 1.32로 측정됐다."),
    ("SOC 사업 경제성", "오늘 점심 메뉴는 김치찌개입니다."),
    ("예비타당성조사 제도", "기획재정부는 예타 면제 기준을 강화한다고 밝혔다."),
    ("예비타당성조사 제도", "강아지 산책 시 주의사항 10가지."),
    ("지역사회 통합돌봄", "지자체별 통합돌봄 시범사업 성과 분석."),
    ("지역사회 통합돌봄", "최근 K-pop 음악 트렌드 분석 보고서."),
    ("공공데이터 활용", "공공데이터포털 API를 이용한 분석 사례."),
    ("공공데이터 활용", "겨울철 자동차 점검 요령."),
]


@dataclass
class ThresholdEval:
    ok: bool
    failures: list[str]


def directory_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return total / (1024 * 1024)


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024) if path.exists() else 0.0


def compute_stats(times: list[float]) -> dict[str, float]:
    arr = np.asarray(times, dtype=np.float64)
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p95": float(np.percentile(arr, 95)),
    }


def evaluate_thresholds(
    *,
    size_mb: float,
    accuracy_loss: float,
    pair50_sec: float,
) -> ThresholdEval:
    failures: list[str] = []
    if size_mb > MAX_SIZE_MB:
        failures.append(f"size_mb={size_mb:.1f} > {MAX_SIZE_MB:.0f}")
    if accuracy_loss > MAX_ACCURACY_LOSS:
        failures.append(f"accuracy_loss={accuracy_loss:.4f} > {MAX_ACCURACY_LOSS:.2f}")
    if pair50_sec > MAX_50PAIR_SEC:
        failures.append(f"pair50_sec={pair50_sec:.2f} > {MAX_50PAIR_SEC:.1f}")
    return ThresholdEval(ok=not failures, failures=failures)


def pre_check() -> None:
    print("=== 사전 확인 ===")
    for pkg in ("optimum", "onnxruntime", "transformers", "onnx"):
        try:
            print(f"  {pkg:22s}: {version(pkg)}")
        except Exception as e:
            print(f"  {pkg:22s}: <{type(e).__name__}: {e}>")
    print(f"  HF_HOME             : {os.environ.get('HF_HOME', '(unset)')}")
    print()

    from optimum.exporters.onnx import main_export

    print("[main_export signature]")
    print(f"  {inspect.signature(main_export)}")
    print()

    from onnxruntime.quantization import QuantType, quantize_dynamic

    print("[quantize_dynamic signature]")
    print(f"  {inspect.signature(quantize_dynamic)}")
    print(f"  QuantType.QInt8 = {QuantType.QInt8}")


def export_onnx_fp32(output_dir: Path) -> Path:
    from optimum.exporters.onnx import main_export

    logger.info("reranker.onnx.export.started", model=MODEL_ID, output_dir=str(output_dir))
    t0 = time.perf_counter()
    # text-classification task로 export — cross-encoder는 sequence classifier 헤드를 사용.
    main_export(
        MODEL_ID,
        output=str(output_dir),
        task="text-classification",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    size_mb = directory_size_mb(output_dir)
    logger.info(
        "reranker.onnx.export.completed",
        model_path=str(output_dir),
        size_mb=round(size_mb, 1),
        elapsed_ms=round(elapsed_ms, 1),
    )
    return output_dir / MODEL_FILE


def quantize_int8(fp32_dir: Path, int8_dir: Path) -> Path:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    int8_dir.mkdir(parents=True, exist_ok=True)
    fp32_model = fp32_dir / MODEL_FILE
    int8_model = int8_dir / MODEL_FILE

    logger.info("reranker.quantize.started", input=str(fp32_model), output=str(int8_model))
    t0 = time.perf_counter()
    quantize_dynamic(
        model_input=str(fp32_model),
        model_output=str(int8_model),
        weight_type=QuantType.QInt8,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    for fname in TOKENIZER_FILES:
        src = fp32_dir / fname
        dst = int8_dir / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    logger.info(
        "reranker.quantize.completed",
        model_path=str(int8_model),
        size_mb=round(file_size_mb(int8_model), 1),
        elapsed_ms=round(elapsed_ms, 1),
    )
    return int8_model


def score_torch(model, tokenizer, pairs: list[tuple[str, str]]) -> np.ndarray:
    import torch

    queries = [p[0] for p in pairs]
    passages = [p[1] for p in pairs]
    enc = tokenizer(
        queries,
        passages,
        padding=True,
        truncation="only_second",
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        out = model(**enc)
    # logits shape: (batch, 1) — squeeze로 (batch,) 만듦.
    return out.logits.squeeze(-1).cpu().numpy().astype(np.float32)


def score_onnx(session, tokenizer, pairs: list[tuple[str, str]]) -> np.ndarray:
    queries = [p[0] for p in pairs]
    passages = [p[1] for p in pairs]
    enc = tokenizer(
        queries,
        passages,
        padding=True,
        truncation="only_second",
        max_length=512,
        return_tensors="np",
    )
    inputs = {
        "input_ids": enc["input_ids"].astype(np.int64),
        "attention_mask": enc["attention_mask"].astype(np.int64),
    }
    outputs = session.run(None, inputs)
    return outputs[0].squeeze(-1).astype(np.float32)


def benchmark_pairs(
    fn: Callable[[list[tuple[str, str]]], np.ndarray],
    pairs: list[tuple[str, str]],
    repeats: int = 3,
) -> list[float]:
    """Return per-50pair seconds for ``repeats`` runs."""
    runs: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(pairs)
        runs.append(time.perf_counter() - t0)
    return runs


def write_report(
    *,
    report_path: Path,
    torch_size_mb: float,
    fp32_size_mb: float,
    int8_size_mb: float,
    int8_dir_size_mb: float,
    torch_scores: np.ndarray,
    fp32_scores: np.ndarray,
    int8_scores: np.ndarray,
    torch_stats: dict[str, float],
    fp32_stats: dict[str, float],
    int8_stats: dict[str, float],
    verdict: ThresholdEval,
    benchmark_n_pairs: int,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()

    # 정확도 손실: PyTorch logit과의 평균 절대 차이의 비례 (sigmoid 적용 후 확률 차이로 환산).
    int8_loss = float(np.mean(np.abs(_sigmoid(torch_scores) - _sigmoid(int8_scores))))
    fp32_loss = float(np.mean(np.abs(_sigmoid(torch_scores) - _sigmoid(fp32_scores))))

    size_table = "\n".join(
        [
            "| 형식 | 크기 (MB) | 비율 |",
            "|---|---|---|",
            f"| PyTorch FP32 (parameters) | {torch_size_mb:.1f} | 100% |",
            f"| ONNX FP32 | {fp32_size_mb:.1f} | {fp32_size_mb / torch_size_mb * 100:.1f}% |",
            f"| ONNX INT8 | {int8_size_mb:.1f} | {int8_size_mb / torch_size_mb * 100:.1f}% |",
        ]
    )
    acc_table = "\n".join(
        [
            "| 비교 | 평균 |sigmoid(score)| 차이 |",
            "|---|---|",
            f"| ONNX FP32 vs PyTorch | {fp32_loss:.4f} |",
            f"| ONNX INT8 vs PyTorch | {int8_loss:.4f} |",
        ]
    )
    speed_table = "\n".join(
        [
            f"| 형식 | 평균 (s/{benchmark_n_pairs}쌍) | 중앙값 | p95 | 속도 향상 |",
            "|---|---|---|---|---|",
            f"| PyTorch CPU | {torch_stats['mean']:.2f} | {torch_stats['median']:.2f} "
            f"| {torch_stats['p95']:.2f} | 1.00x |",
            f"| ONNX FP32 CPU | {fp32_stats['mean']:.2f} | {fp32_stats['median']:.2f} "
            f"| {fp32_stats['p95']:.2f} | "
            f"{torch_stats['mean'] / fp32_stats['mean']:.2f}x |",
            f"| ONNX INT8 CPU | {int8_stats['mean']:.2f} | {int8_stats['median']:.2f} "
            f"| {int8_stats['p95']:.2f} | "
            f"{torch_stats['mean'] / int8_stats['mean']:.2f}x |",
        ]
    )
    pass_table = "\n".join(
        [
            "| 기준 | 목표 | 실측 | 통과 |",
            "|---|---|---|---|",
            f"| 모델 크기 | < {MAX_SIZE_MB:.0f}MB | {int8_dir_size_mb:.1f}MB | "
            f"{'✓' if int8_dir_size_mb <= MAX_SIZE_MB else '✗'} |",
            f"| 정확도 손실 | < {MAX_ACCURACY_LOSS * 100:.0f}% | {int8_loss * 100:.2f}% | "
            f"{'✓' if int8_loss <= MAX_ACCURACY_LOSS else '✗'} |",
            f"| 50쌍 시간 | < {MAX_50PAIR_SEC:.1f}s | {int8_stats['mean']:.2f}s | "
            f"{'✓' if int8_stats['mean'] <= MAX_50PAIR_SEC else '✗'} |",
        ]
    )

    verdict_block = (
        "**판정: PASS** ✓ — 합격 기준 3개 모두 통과."
        if verdict.ok
        else "**판정: FAIL** ✗\n\n실패 항목:\n" + "\n".join(f"- `{f}`" for f in verdict.failures)
    )

    body = f"""# BGE Reranker v2-m3 ONNX·INT8 변환 리포트

생성일: {now}
optimum: {version("optimum")}
onnxruntime: {version("onnxruntime")}
transformers: {version("transformers")}

## 모델 크기

{size_table}

`{INT8_DIR}` 전체 크기: **{int8_dir_size_mb:.1f} MB**

## 정확도 (한국어 {len(KOREAN_PAIRS)}쌍 sigmoid 차이)

{acc_table}

## 속도 ({benchmark_n_pairs}쌍, 3회 반복 stats)

{speed_table}

## 합격 기준 평가

{pass_table}

## 결론

{verdict_block}

## 측정 환경

- PM 노트북 CPU (ZenBook UX482EG, 4P/8L cores, 15.3 GB RAM)
- 측정 데이터셋: 한국어 (query, passage) 쌍 {benchmark_n_pairs}개
- 3회 반복 평균/중앙값/p95
"""
    report_path.write_text(body, encoding="utf-8")


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BGE Reranker v2-m3 ONNX + INT8 setup")
    p.add_argument("--force", action="store_true", help="기존 fp32/int8 산출물 무시하고 재실행")
    p.add_argument(
        "--benchmark-pairs",
        type=int,
        default=50,
        help="속도 측정 시 사용할 쌍 수 (기본 50)",
    )
    p.add_argument(
        "--pre-check",
        action="store_true",
        help="사전 확인 인스펙션만 출력하고 종료",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging()

    if args.pre_check:
        pre_check()
        return 0

    logger.info("reranker.setup.started", force=args.force, n_benchmark=args.benchmark_pairs)

    print("=== Step 1: 환경 확인 ===")
    for pkg in ("optimum", "onnxruntime", "transformers"):
        print(f"  {pkg}: {version(pkg)}")
    hf_home = os.environ.get("HF_HOME", "(unset)")
    print(f"  HF_HOME: {hf_home}")
    Path("./models").mkdir(exist_ok=True)
    print()

    fp32_model = FP32_DIR / MODEL_FILE
    if args.force or not fp32_model.exists():
        print("=== Step 2: ONNX FP32 export ===")
        export_onnx_fp32(FP32_DIR)
    else:
        print(f"=== Step 2: ONNX FP32 export — skipped (cached at {fp32_model}) ===")
    fp32_size_mb = file_size_mb(fp32_model) + file_size_mb(fp32_model.with_suffix(".onnx_data"))

    int8_model = INT8_DIR / MODEL_FILE
    if args.force or not int8_model.exists():
        print("=== Step 3: INT8 양자화 ===")
        quantize_int8(FP32_DIR, INT8_DIR)
    else:
        print(f"=== Step 3: INT8 양자화 — skipped (cached at {int8_model}) ===")
        for fname in TOKENIZER_FILES:
            src = FP32_DIR / fname
            dst = INT8_DIR / fname
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
    int8_size_mb = file_size_mb(int8_model)
    int8_dir_size_mb = directory_size_mb(INT8_DIR)

    print("=== Step 4: 정확도 검증 (PyTorch / ONNX FP32 / ONNX INT8) ===")
    import onnxruntime as ort
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch_model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID).eval()
    torch_size_mb = sum(p.numel() * p.element_size() for p in torch_model.parameters()) / (
        1024 * 1024
    )
    tokenizer = AutoTokenizer.from_pretrained(str(INT8_DIR))
    fp32_session = ort.InferenceSession(str(fp32_model), providers=["CPUExecutionProvider"])
    int8_session = ort.InferenceSession(str(int8_model), providers=["CPUExecutionProvider"])

    torch_scores = score_torch(torch_model, tokenizer, KOREAN_PAIRS)
    fp32_scores = score_onnx(fp32_session, tokenizer, KOREAN_PAIRS)
    int8_scores = score_onnx(int8_session, tokenizer, KOREAN_PAIRS)

    accuracy_loss = float(np.mean(np.abs(_sigmoid(torch_scores) - _sigmoid(int8_scores))))
    print(f"  ONNX FP32 logits: {fp32_scores}")
    print(f"  ONNX INT8 logits: {int8_scores}")
    print(f"  INT8 정확도 손실 (|sigmoid Δ| 평균): {accuracy_loss:.4f}")
    print()

    print(f"=== Step 5: 속도 벤치마크 (n={args.benchmark_pairs}, 3회 반복) ===")
    bench_pairs = (KOREAN_PAIRS * (args.benchmark_pairs // len(KOREAN_PAIRS) + 1))[
        : args.benchmark_pairs
    ]
    torch_runs = benchmark_pairs(lambda p: score_torch(torch_model, tokenizer, p), bench_pairs)
    fp32_runs = benchmark_pairs(lambda p: score_onnx(fp32_session, tokenizer, p), bench_pairs)
    int8_runs = benchmark_pairs(lambda p: score_onnx(int8_session, tokenizer, p), bench_pairs)
    torch_stats = compute_stats(torch_runs)
    fp32_stats = compute_stats(fp32_runs)
    int8_stats = compute_stats(int8_runs)

    print(f"  PyTorch  : mean={torch_stats['mean']:.2f}s")
    print(f"  ONNX FP32: mean={fp32_stats['mean']:.2f}s")
    print(f"  ONNX INT8: mean={int8_stats['mean']:.2f}s")

    verdict = evaluate_thresholds(
        size_mb=int8_dir_size_mb,
        accuracy_loss=accuracy_loss,
        pair50_sec=int8_stats["mean"],
    )
    write_report(
        report_path=REPORT_PATH,
        torch_size_mb=torch_size_mb,
        fp32_size_mb=fp32_size_mb,
        int8_size_mb=int8_size_mb,
        int8_dir_size_mb=int8_dir_size_mb,
        torch_scores=torch_scores,
        fp32_scores=fp32_scores,
        int8_scores=int8_scores,
        torch_stats=torch_stats,
        fp32_stats=fp32_stats,
        int8_stats=int8_stats,
        verdict=verdict,
        benchmark_n_pairs=len(bench_pairs),
    )
    logger.info("reranker.report.written", path=str(REPORT_PATH))

    # FP32 임시 산출물은 INT8 양자화에 쓰고 남기지 않음 — 디스크 회수.
    if FP32_DIR.exists():
        logger.info("reranker.fp32.cleanup", path=str(FP32_DIR))
        shutil.rmtree(FP32_DIR)

    if not verdict.ok:
        logger.error("reranker.setup.failed", failures=verdict.failures)
        print(f"\n[FAIL] 합격 기준 미달: {verdict.failures}")
        print(f"리포트: {REPORT_PATH}")
        return 1

    print(f"\n[PASS] 합격 기준 3개 모두 통과. 리포트: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
