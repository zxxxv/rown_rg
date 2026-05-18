"""Ad-hoc benchmark: CPU INT8 vs GPU FP16 on parsed hwpx/pdf paragraphs.

Pinned to the dtype/device policy decided for this project:
    CPU only         -> bge-m3-onnx-int8   (AVX-VNNI accelerated)
    Turing GPU       -> bge-m3-onnx-fp16   (no Tensor Core => INT8 unprofitable)

The script parses one hwpx + one pdf, caps paragraphs at 200 per source, and
measures embedding latency/throughput + cross-dtype cosine similarity. It does
not modify ``benchmark_embedding.py`` because the standing benchmark assumes a
single dtype.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clients.embedding_client import BgeM3Client, EmbeddingCache

# 너무 많은 paragraph는 측정 길이만 키움 — 200개면 CPU 1~2분, GPU 수초로 차이가 충분히 드러남.
PARAGRAPH_CAP = 200
# docling이 GPU에 모델을 잡고 안 풀어서 측정 프로세스 안에서 파싱하면 OOM 위험 — 사전 파싱
# 산출물을 디스크에서 읽도록 분리.
INPUTS_DIR = Path("/tmp/bench_inputs")


def now() -> str:
    return time.strftime("%H:%M:%S")


def split_paragraphs(markdown: str) -> list[str]:
    return [p.strip() for p in markdown.split("\n\n") if p.strip()]


def gpu_snapshot(label: str) -> None:
    try:
        p = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        print(f"  [{now()}] GPU {label:14s} {p.stdout.strip()}", flush=True)
    except Exception as e:
        print(f"  [{now()}] GPU snapshot fail: {e}", flush=True)


async def measure(
    label: str,
    model_path: str,
    device: str,
    paras: list[str],
) -> tuple[float, np.ndarray]:
    # 캐시 디렉토리를 임시로 분리해 dtype 사이 cache contamination을 차단.
    with tempfile.TemporaryDirectory() as cache_dir:
        client = BgeM3Client(
            model_path=model_path,
            device=device,
            cache=EmbeddingCache(root=Path(cache_dir)),
        )
        # 워밍업 — 첫 forward는 JIT/CUDA graph 초기화 비용이 측정에 끼인다.
        await client.embed_batch(paras[:5])

        t0 = time.perf_counter()
        results = await client.embed_batch(paras)
        elapsed = time.perf_counter() - t0

        vecs = np.array([r.embedding for r in results], dtype=np.float32)
        print(
            f"  [{now()}] {label:10s} {elapsed:6.2f}s  {len(paras) / elapsed:6.1f} para/s",
            flush=True,
        )
        return elapsed, vecs


def cosine_pairs(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    a_n = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_n = b / np.linalg.norm(b, axis=1, keepdims=True)
    sims = (a_n * b_n).sum(axis=1)
    return {
        "mean": float(sims.mean()),
        "min": float(sims.min()),
        "p5": float(np.percentile(sims, 5)),
    }


async def amain() -> None:
    print(f"[{now()}] === loading pre-parsed sources ===")
    hwpx_md = (INPUTS_DIR / "hwpx.md").read_text()
    pdf_md = (INPUTS_DIR / "pdf.md").read_text()
    hwpx_paras = split_paragraphs(hwpx_md)[:PARAGRAPH_CAP]
    pdf_paras = split_paragraphs(pdf_md)[:PARAGRAPH_CAP]
    print(f"  hwpx: {len(hwpx_paras)} paras (capped)", flush=True)
    print(f"  pdf : {len(pdf_paras)} paras (capped)", flush=True)

    print()
    print(f"[{now()}] === benchmarks ===")
    for input_label, paras in [("hwpx", hwpx_paras), ("pdf", pdf_paras)]:
        print(f"\n--- {input_label} ({len(paras)} paras) ---")
        gpu_snapshot("before")
        t_cpu, vec_cpu = await measure("CPU INT8", "models/bge-m3-onnx-int8", "cpu", paras)
        gpu_snapshot("after_cpu")
        t_gpu, vec_gpu = await measure("GPU FP16", "models/bge-m3-onnx-fp16", "cuda", paras)
        gpu_snapshot("after_gpu")

        speedup = t_cpu / t_gpu if t_gpu > 0 else float("inf")
        sims = cosine_pairs(vec_cpu, vec_gpu)
        print(
            f"  speedup x{speedup:.1f}  "
            f"cosine(INT8 vs FP16): mean={sims['mean']:.4f} "
            f"min={sims['min']:.4f} p5={sims['p5']:.4f}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(amain())
