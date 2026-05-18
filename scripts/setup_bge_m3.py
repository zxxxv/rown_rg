"""BGE-M3 ONNX 변환 + int8 동적 양자화 셋업 CLI.

사용법:
    uv run python scripts/setup_bge_m3.py [--force] [--benchmark-samples 100] [--pre-check]

--pre-check: 코드 작성 전 가정 검증용 인스펙션 출력만 하고 종료.
--force:     캐시된 fp32/int8 산출물을 무시하고 다시 변환.
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

MODEL_ID = "BAAI/bge-m3"
FP32_DIR = Path("./models/bge-m3-onnx-fp32")
INT8_DIR = Path("./models/bge-m3-onnx-int8")
MODEL_FILE = "model.onnx"
REPORT_PATH = Path("./reports/bge_m3_setup.md")
TOKENIZER_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
)

# 합격 기준
MAX_SIZE_MB = 700.0
MAX_ACCURACY_LOSS = 0.05
MIN_SPEEDUP = 2.0
MAX_PARA_MS = 300.0

# 한국어 평가 샘플 (정확도/속도 측정용 짧은 토픽 문구)
KOREAN_SAMPLES: list[str] = [
    "2023년 노인실태조사 결과 발표",
    "예비타당성조사 제도 개정 동향",
    "기획재정부의 사업 평가 기준",
    "SOC 사업의 경제성 분석",
    "고령화 사회의 정책적 함의",
    "한국개발연구원의 연구보고서",
    "지역사회 통합돌봄 추진 현황",
    "노인 일자리 사업 확대 방안",
    "복지 사각지대 해소 정책",
    "공공데이터포털 활용 사례",
]


@dataclass
class ThresholdEval:
    ok: bool
    failures: list[str]


# ---------- 헬퍼 함수들 (단위 테스트 대상) ----------


def directory_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return total / (1024 * 1024)


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024) if path.exists() else 0.0


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """두 1차원 벡터의 코사인 유사도. 영벡터 입력 시 0 반환(NaN 방지)."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def mean_cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """두 (N, D) 임베딩 행렬의 행별 코사인 유사도 평균."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    return float(np.mean(np.sum(a_norm * b_norm, axis=1)))


def compute_stats(times: list[float]) -> dict[str, float]:
    """ms 리스트의 mean/median/p95 (np.percentile 선형 보간)."""
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
    speedup: float,
    ms_per_para: float,
) -> ThresholdEval:
    failures: list[str] = []
    if size_mb > MAX_SIZE_MB:
        failures.append(f"size_mb={size_mb:.1f} > {MAX_SIZE_MB:.0f}")
    if accuracy_loss > MAX_ACCURACY_LOSS:
        failures.append(f"accuracy_loss={accuracy_loss:.4f} > {MAX_ACCURACY_LOSS:.2f}")
    if speedup < MIN_SPEEDUP:
        failures.append(f"speedup={speedup:.2f}x < {MIN_SPEEDUP:.1f}x")
    if ms_per_para > MAX_PARA_MS:
        failures.append(f"ms_per_para={ms_per_para:.1f}ms > {MAX_PARA_MS:.0f}ms")
    return ThresholdEval(ok=not failures, failures=failures)


# ---------- 사전 확인 (인스펙션) ----------


def pre_check() -> None:
    """가정 검증용 인스펙션 출력."""
    print("=== 사전 확인 ===")
    for pkg in ("optimum", "onnxruntime", "transformers", "sentence-transformers", "onnx"):
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
    print()


# ---------- 변환 파이프라인 ----------


def export_onnx_fp32(output_dir: Path) -> Path:
    from optimum.exporters.onnx import main_export

    logger.info("bge_m3.onnx.export.started", model=MODEL_ID, output_dir=str(output_dir))
    t0 = time.perf_counter()
    main_export(
        MODEL_ID,
        output=str(output_dir),
        task="feature-extraction",
        library_name="sentence_transformers",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    size_mb = directory_size_mb(output_dir)
    logger.info(
        "bge_m3.onnx.export.completed",
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

    logger.info("bge_m3.quantize.started", input=str(fp32_model), output=str(int8_model))
    t0 = time.perf_counter()
    quantize_dynamic(
        model_input=str(fp32_model),
        model_output=str(int8_model),
        weight_type=QuantType.QInt8,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # 토크나이저/config를 int8 디렉토리에도 복사 (배포 시 단일 dir 로드용)
    for fname in TOKENIZER_FILES:
        src = fp32_dir / fname
        dst = int8_dir / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    logger.info(
        "bge_m3.quantize.completed",
        model_path=str(int8_model),
        size_mb=round(file_size_mb(int8_model), 1),
        elapsed_ms=round(elapsed_ms, 1),
    )
    return int8_model


# ---------- 임베딩 ----------


def embed_torch(model, texts: list[str]) -> np.ndarray:
    """sentence-transformers 임베딩 (L2 정규화된 dense 벡터)."""
    out = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return np.asarray(out, dtype=np.float32)


def embed_onnx(session, tokenizer, texts: list[str]) -> np.ndarray:
    """ONNX 세션으로 임베딩 — CLS pooling + L2 정규화 (BGE-M3 표준)."""
    enc = tokenizer(texts, padding=True, truncation=True, return_tensors="np", max_length=512)
    inputs = {
        "input_ids": enc["input_ids"].astype(np.int64),
        "attention_mask": enc["attention_mask"].astype(np.int64),
    }
    outputs = session.run(None, inputs)
    last_hidden = outputs[0]  # (batch, seq, hidden)
    cls = last_hidden[:, 0, :]
    norms = np.linalg.norm(cls, axis=1, keepdims=True)
    return cls / np.clip(norms, a_min=1e-12, a_max=None)


# ---------- 벤치마크 ----------


def benchmark(
    fn: Callable[[list[str]], np.ndarray],
    texts: list[str],
    repeats: int = 3,
) -> list[float]:
    """fn(texts)를 repeats회 실행, 매회 단락당 평균 ms를 리스트로 반환."""
    runs: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(texts)
        total_ms = (time.perf_counter() - t0) * 1000.0
        runs.append(total_ms / len(texts))
    return runs


# ---------- 리포트 ----------


def write_report(
    *,
    report_path: Path,
    torch_size_mb: float,
    fp32_size_mb: float,
    int8_size_mb: float,
    int8_dir_size_mb: float,
    fp32_vs_torch: float,
    int8_vs_torch: float,
    int8_vs_fp32: float,
    torch_stats: dict[str, float],
    fp32_stats: dict[str, float],
    int8_stats: dict[str, float],
    speedup: float,
    ms_per_para: float,
    verdict: ThresholdEval,
    benchmark_n: int,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()

    def _pct(v: float) -> float:
        return (1.0 - v) * 100.0

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
            "| 비교 | 평균 유사도 | 손실 |",
            "|---|---|---|",
            f"| ONNX FP32 vs PyTorch | {fp32_vs_torch:.4f} | {_pct(fp32_vs_torch):.2f}% |",
            f"| ONNX INT8 vs PyTorch | {int8_vs_torch:.4f} | {_pct(int8_vs_torch):.2f}% |",
            f"| ONNX INT8 vs ONNX FP32 | {int8_vs_fp32:.4f} | {_pct(int8_vs_fp32):.2f}% |",
        ]
    )
    speed_table = "\n".join(
        [
            "| 형식 | 평균 ms | 중앙값 ms | p95 ms | 속도 향상 |",
            "|---|---|---|---|---|",
            f"| PyTorch CPU | {torch_stats['mean']:.1f} | {torch_stats['median']:.1f} "
            f"| {torch_stats['p95']:.1f} | 1.00x |",
            f"| ONNX FP32 CPU | {fp32_stats['mean']:.1f} | {fp32_stats['median']:.1f} "
            f"| {fp32_stats['p95']:.1f} | {torch_stats['mean'] / fp32_stats['mean']:.2f}x |",
            f"| ONNX INT8 CPU | {int8_stats['mean']:.1f} | {int8_stats['median']:.1f} "
            f"| {int8_stats['p95']:.1f} | {speedup:.2f}x |",
        ]
    )
    int8_loss = 1.0 - int8_vs_torch
    pass_table = "\n".join(
        [
            "| 기준 | 목표 | 실측 | 통과 |",
            "|---|---|---|---|",
            f"| 모델 크기 | < {MAX_SIZE_MB:.0f}MB | {int8_dir_size_mb:.1f}MB | "
            f"{'✓' if int8_dir_size_mb <= MAX_SIZE_MB else '✗'} |",
            f"| 정확도 손실 | < {MAX_ACCURACY_LOSS * 100:.0f}% | {int8_loss * 100:.2f}% | "
            f"{'✓' if int8_loss <= MAX_ACCURACY_LOSS else '✗'} |",
            f"| 속도 향상 | > {MIN_SPEEDUP:.1f}배 | {speedup:.2f}배 | "
            f"{'✓' if speedup >= MIN_SPEEDUP else '✗'} |",
            f"| 단락당 평균 | < {MAX_PARA_MS:.0f}ms | {ms_per_para:.1f}ms | "
            f"{'✓' if ms_per_para <= MAX_PARA_MS else '✗'} |",
        ]
    )

    verdict_block = (
        "**판정: PASS** ✓ — 합격 기준 4개 모두 통과."
        if verdict.ok
        else "**판정: FAIL** ✗\n\n실패 항목:\n" + "\n".join(f"- `{f}`" for f in verdict.failures)
    )

    body = f"""# BGE-M3 ONNX·INT8 변환 리포트

생성일: {now}
optimum: {version("optimum")}
onnxruntime: {version("onnxruntime")}
transformers: {version("transformers")}
sentence-transformers: {version("sentence-transformers")}

## 모델 크기

{size_table}

`{INT8_DIR}` 전체 크기: **{int8_dir_size_mb:.1f} MB**

## 정확도 (한국어 {len(KOREAN_SAMPLES)}문장 코사인 유사도)

{acc_table}

## 속도 ({benchmark_n}문장, median of 3회 반복)

{speed_table}

## 합격 기준 평가

{pass_table}

## 결론

{verdict_block}
"""
    report_path.write_text(body, encoding="utf-8")


# ---------- 메인 ----------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BGE-M3 ONNX + int8 setup")
    p.add_argument("--force", action="store_true", help="기존 fp32/int8 산출물 무시하고 재실행")
    p.add_argument(
        "--benchmark-samples",
        type=int,
        default=100,
        help="속도 측정 시 사용할 문장 수 (기본 100)",
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

    logger.info("bge_m3.setup.started", force=args.force, n_benchmark=args.benchmark_samples)

    # Step 1: 환경 확인
    print("=== Step 1: 환경 확인 ===")
    for pkg in ("optimum", "onnxruntime", "transformers", "sentence-transformers"):
        print(f"  {pkg}: {version(pkg)}")
    hf_home = os.environ.get("HF_HOME", "(unset)")
    print(f"  HF_HOME: {hf_home}")
    if hf_home == "(unset)":
        print(
            "  [WARN] HF_HOME 미설정 — ~/.cache/huggingface로 떨어집니다. .env 로드를 확인하세요."
        )
    Path("./models").mkdir(exist_ok=True)
    print()

    # Step 2: ONNX FP32 export (캐시)
    fp32_model = FP32_DIR / MODEL_FILE
    if args.force or not fp32_model.exists():
        print("=== Step 2: ONNX FP32 export ===")
        export_onnx_fp32(FP32_DIR)
    else:
        logger.info("bge_m3.onnx.export.skipped", reason="cached", path=str(fp32_model))
        print(f"=== Step 2: ONNX FP32 export — skipped (cached at {fp32_model}) ===")
    fp32_size_mb = file_size_mb(fp32_model) + file_size_mb(fp32_model.with_suffix(".onnx_data"))

    # Step 3: int8 양자화 (캐시)
    int8_model = INT8_DIR / MODEL_FILE
    if args.force or not int8_model.exists():
        print("=== Step 3: int8 양자화 ===")
        quantize_int8(FP32_DIR, INT8_DIR)
    else:
        logger.info("bge_m3.quantize.skipped", reason="cached", path=str(int8_model))
        print(f"=== Step 3: int8 양자화 — skipped (cached at {int8_model}) ===")
        # 토크나이저가 빠져있을 수 있으니 보정
        for fname in TOKENIZER_FILES:
            src = FP32_DIR / fname
            dst = INT8_DIR / fname
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
    int8_size_mb = file_size_mb(int8_model)
    int8_dir_size_mb = directory_size_mb(INT8_DIR)

    # Step 4: 정확도 검증 (3-way)
    print("=== Step 4: 정확도 검증 (PyTorch / ONNX FP32 / ONNX INT8) ===")
    import onnxruntime as ort
    from sentence_transformers import SentenceTransformer
    from transformers import AutoTokenizer

    torch_model = SentenceTransformer(MODEL_ID, device="cpu")
    torch_size_mb = sum(
        p.numel() * p.element_size() for p in torch_model._first_module().auto_model.parameters()
    ) / (1024 * 1024)
    tokenizer = AutoTokenizer.from_pretrained(str(INT8_DIR))
    fp32_session = ort.InferenceSession(str(fp32_model), providers=["CPUExecutionProvider"])
    int8_session = ort.InferenceSession(str(int8_model), providers=["CPUExecutionProvider"])

    torch_emb = embed_torch(torch_model, KOREAN_SAMPLES)
    fp32_emb = embed_onnx(fp32_session, tokenizer, KOREAN_SAMPLES)
    int8_emb = embed_onnx(int8_session, tokenizer, KOREAN_SAMPLES)

    fp32_vs_torch = mean_cosine_similarity(torch_emb, fp32_emb)
    int8_vs_torch = mean_cosine_similarity(torch_emb, int8_emb)
    int8_vs_fp32 = mean_cosine_similarity(fp32_emb, int8_emb)
    accuracy_loss = 1.0 - int8_vs_torch

    print(f"  ONNX FP32 vs PyTorch : {fp32_vs_torch:.4f}")
    print(f"  ONNX INT8 vs PyTorch : {int8_vs_torch:.4f}")
    print(f"  ONNX INT8 vs FP32    : {int8_vs_fp32:.4f}")
    print()

    # Step 5: 속도 벤치마크
    print(f"=== Step 5: 속도 벤치마크 (n={args.benchmark_samples}, 3회 반복) ===")
    sample_texts = (KOREAN_SAMPLES * (args.benchmark_samples // len(KOREAN_SAMPLES) + 1))[
        : args.benchmark_samples
    ]
    logger.info("bge_m3.benchmark.started", n=len(sample_texts), repeats=3)
    torch_runs = benchmark(lambda t: embed_torch(torch_model, t), sample_texts)
    fp32_runs = benchmark(lambda t: embed_onnx(fp32_session, tokenizer, t), sample_texts)
    int8_runs = benchmark(lambda t: embed_onnx(int8_session, tokenizer, t), sample_texts)

    torch_stats = compute_stats(torch_runs)
    fp32_stats = compute_stats(fp32_runs)
    int8_stats = compute_stats(int8_runs)
    ms_per_para = int8_stats["mean"]
    speedup = torch_stats["mean"] / int8_stats["mean"] if int8_stats["mean"] > 0 else 0.0

    logger.info(
        "bge_m3.benchmark.completed",
        torch_ms_per_para=round(torch_stats["mean"], 2),
        onnx_fp32_ms_per_para=round(fp32_stats["mean"], 2),
        onnx_int8_ms_per_para=round(ms_per_para, 2),
        speedup=round(speedup, 2),
    )
    print(
        f"  PyTorch  : mean={torch_stats['mean']:.1f} median={torch_stats['median']:.1f} "
        f"p95={torch_stats['p95']:.1f} ms/단락"
    )
    print(
        f"  ONNX FP32: mean={fp32_stats['mean']:.1f} median={fp32_stats['median']:.1f} "
        f"p95={fp32_stats['p95']:.1f} ms/단락"
    )
    print(
        f"  ONNX INT8: mean={ms_per_para:.1f} median={int8_stats['median']:.1f} "
        f"p95={int8_stats['p95']:.1f} ms/단락 (speedup={speedup:.2f}x)"
    )
    print()

    # Step 6: 합격 기준 + 리포트
    verdict = evaluate_thresholds(
        size_mb=int8_dir_size_mb,
        accuracy_loss=accuracy_loss,
        speedup=speedup,
        ms_per_para=ms_per_para,
    )
    write_report(
        report_path=REPORT_PATH,
        torch_size_mb=torch_size_mb,
        fp32_size_mb=fp32_size_mb,
        int8_size_mb=int8_size_mb,
        int8_dir_size_mb=int8_dir_size_mb,
        fp32_vs_torch=fp32_vs_torch,
        int8_vs_torch=int8_vs_torch,
        int8_vs_fp32=int8_vs_fp32,
        torch_stats=torch_stats,
        fp32_stats=fp32_stats,
        int8_stats=int8_stats,
        speedup=speedup,
        ms_per_para=ms_per_para,
        verdict=verdict,
        benchmark_n=len(sample_texts),
    )
    logger.info("bge_m3.report.written", path=str(REPORT_PATH))

    if not verdict.ok:
        logger.error("bge_m3.setup.failed", failures=verdict.failures)
        print(f"\n[FAIL] 합격 기준 미달: {verdict.failures}")
        print(f"리포트: {REPORT_PATH}")
        return 1

    print(f"\n[PASS] 4개 합격 기준 모두 통과. 리포트: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
