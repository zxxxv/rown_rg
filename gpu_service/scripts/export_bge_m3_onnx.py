"""bge-m3를 GPU용 ONNX로 내보낸다.

**왜 따로 있나.** ``export_reranker_onnx.py``는 시퀀스 분류(cross-encoder)용이고
bge-m3는 특징 추출(feature-extraction)이라 export task가 다르다. fp16 변환만 같은
모듈(``onnx_fp16``)을 공유한다.

**왜 필요한가.** 저장소의 ``models/bge-m3-onnx-int8``은 CPU 전용이다(INT8 양자화
연산자). GPU에는 fp16 내보내기가 따로 있어야 하고, 8GB 카드에 리랭커까지 같이
올리므로 fp32는 선택지가 아니다 — 리랭커 fp32 하나가 이미 4,910MiB였다.

    docker compose -f gpu_service/docker-compose.yml --profile setup run --rm exporter-m3

또는 저장소 venv가 있는 곳에서:

    uv run python gpu_service/scripts/export_bge_m3_onnx.py \\
        --out models/bge-m3-onnx-fp16 --fp16 --verify

``--verify``는 기존 int8 모델과 **같은 문장의 코사인 유사도**를 잰다. 리랭커의
``--verify``가 순위 뒤집힘을 보는 것과 목적이 다르다: 임베딩은 벡터가 색인에
저장되므로 "얼마나 같은 공간인가"가 곧 검색 품질이다. 0.99 아래면 재색인 없이
섞어 쓰면 안 된다.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from gpu_service.scripts.onnx_fp16 import SIDECAR_FILES, to_fp16

MODEL_ID = "BAAI/bge-m3"

# 유사도 검증용 표본 — 답이 명확해 사람이 눈으로 검산할 수 있다.
_VERIFY_TEXTS = [
    "탄소국경조정제도는 시멘트, 철강, 알루미늄, 비료, 전력, 수소를 적용 대상으로 한다.",
    "CBAM의 대상 품목에는 철강과 알루미늄이 포함된다.",
    "이 보고서의 표지는 국문과 영문을 병기한다.",
]


def export(model_id: str, out_dir: Path) -> None:
    from optimum.exporters.onnx import main_export

    print(f"내보내는 중: {model_id} → {out_dir}")
    # library_name을 "transformers"로 고정한다 — sentence_transformers 경로는
    # optimum 2.x + sentence-transformers 5.x 조합에서 깨진다(모델 config가 읽기전용
    # property로 바뀌어 standardize_model_attributes가 AttributeError). 어차피
    # 그래프 출력은 동일한 last_hidden_state이고 CLS 풀링은 알맹이가 한다.
    # setup_bge_m3.py와 같은 설정이어야 기존 색인과 같은 공간이 나온다.
    main_export(
        model_id,
        output=str(out_dir),
        task="feature-extraction",
        library_name="transformers",
    )
    if not (out_dir / "model.onnx").exists():
        candidates = sorted(out_dir.glob("*.onnx"))
        if not candidates:
            raise SystemExit(f"내보낸 .onnx가 없습니다: {out_dir}")
        candidates[0].rename(out_dir / "model.onnx")
    print(f"완료: {out_dir}")


def _embed(model_dir: Path, texts: list[str]):
    from src.clients.onnx_text_embedder import OnnxTextEmbedder

    return OnnxTextEmbedder(model_dir).embed(texts)


def verify(new_dir: Path, baseline_dir: Path | None) -> None:
    """새 내보내기의 벡터 공간이 기존과 얼마나 같은지 — 코사인 유사도로 본다."""
    import numpy as np

    new = _embed(new_dir, _VERIFY_TEXTS)
    # 벡터는 이미 L2 정규화돼 있으므로 내적이 곧 코사인이다.
    print("\n새 모델 내부 유사도:")
    print(f"  관련 문장쌍 (0-1): {float(new[0] @ new[1]):.4f}")
    print(f"  무관 문장쌍 (0-2): {float(new[0] @ new[2]):.4f}")
    if float(new[0] @ new[1]) <= float(new[0] @ new[2]):
        print("  ★ 관련 문장이 무관 문장보다 가깝지 않다 - 모델/풀링이 잘못됐다")

    if not (baseline_dir and baseline_dir.exists()):
        print("\n(기존 모델이 없어 공간 비교를 건너뜁니다)")
        return

    base = _embed(baseline_dir, _VERIFY_TEXTS)
    per_text = [float(a @ b) for a, b in zip(new, base, strict=True)]
    worst = min(per_text)
    print(f"\n기존({baseline_dir.name}) 대비 같은 문장의 코사인:")
    for i, c in enumerate(per_text):
        print(f"  문장 {i}: {c:.6f}")
    print(f"  최솟값: {worst:.6f}")
    # 0.99는 임의 기준이 아니다 - 이 값 아래면 상위 k개 검색 결과가 실제로 바뀌기
    # 시작한다. 기존 색인을 그대로 두고 새 모델로 질의하려면 이 선을 넘어야 한다.
    print(f"  판정: {'같은 공간으로 볼 수 있음' if worst >= 0.99 else '★ 전량 재색인 필요'}")
    print(f"  최대 절대오차: {float(np.max(np.abs(new - base))):.6f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default=MODEL_ID)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fp16", action="store_true", help="fp32로 내보낸 뒤 fp16으로 변환")
    parser.add_argument("--verify", action="store_true", help="기존 int8 모델과 공간 비교")
    parser.add_argument("--baseline", type=Path, default=Path("models/bge-m3-onnx-int8"))
    parser.add_argument(
        "--keep-fp32", action="store_true", help="fp16 변환 후 중간 fp32 폴더를 남긴다"
    )
    args = parser.parse_args()

    if args.fp16:
        fp32_dir = args.out.parent / f"{args.out.name}-fp32-tmp"
        export(args.model_id, fp32_dir)
        to_fp16(fp32_dir, args.out)
        if not args.keep_fp32:
            # fp32 중간물은 2.3GB다. 남겨 둘 이유가 없으면 지운다.
            shutil.rmtree(fp32_dir, ignore_errors=True)
    else:
        export(args.model_id, args.out)
        # sidecar가 이미 export로 나오지만, 이름이 다른 경우를 대비해 확인만 한다.
        missing = [n for n in SIDECAR_FILES if not (args.out / n).exists()]
        if missing:
            print(f"주의: 다음 파일이 없습니다 → {missing}")

    if args.verify:
        verify(args.out, args.baseline)


if __name__ == "__main__":
    main()
