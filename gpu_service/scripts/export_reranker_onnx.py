"""bge-reranker-v2-m3를 GPU용 ONNX로 내보낸다.

**왜 필요한가.** 지금 저장소의 리랭커는 ``models/bge-reranker-v2-m3-onnx-int8``인데
INT8 양자화는 CPU 전용 연산자를 쓴다 — GPU에서 못 돌린다. GPU 박스에는 fp32(또는
fp16) 내보내기가 따로 있어야 한다.

**어디서 돌리나.** 저장소 venv(optimum·onnx 포함)가 있는 곳이면 어디든. 내보낸
폴더를 GPU 박스의 ``models/`` 아래로 복사하면 된다(fp32 약 2.3GB, fp16 약 1.1GB).

    uv run python gpu_service/scripts/export_reranker_onnx.py --out models/bge-reranker-v2-m3-onnx
    uv run python gpu_service/scripts/export_reranker_onnx.py --fp16 \
        --out models/bge-reranker-v2-m3-onnx-fp16

``--verify``를 주면 기존 INT8 모델과 같은 문장쌍을 채점해 **순위가 뒤집히지 않는지**
확인한다. dtype을 바꿀 때 조용히 품질이 내려가는 것을 여기서 잡는다.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# 순위 뒤집힘 확인용 표본 — 답이 명확한 쌍이라 사람이 눈으로 검산할 수 있다.
_VERIFY_QUERY = "탄소국경조정제도의 적용 대상 품목은 무엇인가"
_VERIFY_PASSAGES = [
    "CBAM은 시멘트, 철강, 알루미늄, 비료, 전력, 수소 6개 품목을 적용 대상으로 한다.",
    "전환기간 동안 수입업자는 분기별로 내재배출량을 보고할 의무를 진다.",
    "이 보고서의 표지는 국문과 영문을 병기한다.",
]


def export(model_id: str, out_dir: Path) -> None:
    from optimum.onnxruntime import ORTModelForSequenceClassification
    from transformers import AutoTokenizer

    print(f"내보내는 중: {model_id} → {out_dir}")
    model = ORTModelForSequenceClassification.from_pretrained(model_id, export=True)
    model.save_pretrained(out_dir)
    AutoTokenizer.from_pretrained(model_id).save_pretrained(out_dir)  # type: ignore[no-untyped-call]
    # optimum이 model.onnx로 저장하지만 버전에 따라 이름이 다를 수 있다.
    # OnnxCrossEncoder는 model.onnx만 찾으므로 여기서 맞춰 둔다.
    if not (out_dir / "model.onnx").exists():
        candidates = sorted(out_dir.glob("*.onnx"))
        if not candidates:
            raise SystemExit(f"내보낸 .onnx가 없습니다: {out_dir}")
        candidates[0].rename(out_dir / "model.onnx")
    print(f"완료: {out_dir}")


def to_fp16(src_dir: Path, out_dir: Path) -> None:
    """fp32 그래프를 fp16으로 변환 — 가중치 절반, 텐서코어 사용.

    수치가 바뀌므로 반드시 ``--verify``로 순위를 확인하고 쓸 것. 리랭커 점수는
    저장되지 않아 임베딩만큼 위험하지는 않지만, 순위가 바뀌면 근거가 바뀐다.
    """
    import onnx

    try:
        from onnxruntime.transformers.float16 import convert_float_to_float16
    except ImportError:  # onnxruntime 버전에 따라 위치가 다르다
        from onnxconverter_common.float16 import convert_float_to_float16

    out_dir.mkdir(parents=True, exist_ok=True)

    # 2GB 넘는 모델은 여기서 걸린다. convert_float_to_float16이 내부에서
    # onnx.shape_inference.infer_shapes(model)을 부르는데, 그게 model.SerializeToString()을
    # 하고 protobuf는 메시지 하나가 2GB를 못 넘는다. bge-reranker fp32는 2.27GB라
    # EncodeError로 죽는다(2026-08-20). 파일 기반 infer_shapes_path는 그 한계가 없으므로
    # 먼저 돌려 두고, 변환기에는 다시 추론하지 말라고 알린다 - 추론을 생략하는 게 아니라
    # 앞당기는 것이라 변환 품질은 그대로다.
    inferred = src_dir / "_shape_inferred.tmp.onnx"
    try:
        # 출력을 src_dir에 두는 이유: 외부 데이터(model.onnx_data) 참조가 상대 경로라
        # 다른 디렉터리에 쓰면 이어서 load할 때 가중치를 못 찾는다.
        onnx.shape_inference.infer_shapes_path(
            str(src_dir / "model.onnx"), str(inferred)
        )
        model = onnx.load(str(inferred))
    finally:
        inferred.unlink(missing_ok=True)

    # keep_io_types: 입출력은 fp32로 남긴다 - 호출부(int64 입력, float 출력)를 안 바꾸려고.
    converted = convert_float_to_float16(model, keep_io_types=True, disable_shape_infer=True)
    # fp16은 절반이라 2GB 아래로 떨어져 단일 파일로 저장된다(외부 데이터 불필요).
    onnx.save(converted, str(out_dir / "model.onnx"))
    for name in (
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "sentencepiece.bpe.model",
        "config.json",
    ):
        src = src_dir / name
        if src.exists():
            shutil.copy2(src, out_dir / name)
    print(f"fp16 변환 완료: {out_dir}")


def verify(new_dir: Path, baseline_dir: Path | None) -> None:
    from src.clients.onnx_cross_encoder import OnnxCrossEncoder

    new = OnnxCrossEncoder(new_dir).score(_VERIFY_QUERY, _VERIFY_PASSAGES)
    print(f"\n새 모델 점수: {[round(s, 4) for s in new]}")
    if baseline_dir and baseline_dir.exists():
        base = OnnxCrossEncoder(baseline_dir).score(_VERIFY_QUERY, _VERIFY_PASSAGES)
        print(f"기존(int8) 점수: {[round(s, 4) for s in base]}")
        new_order = sorted(range(len(new)), key=lambda i: -new[i])
        base_order = sorted(range(len(base)), key=lambda i: -base[i])
        verdict = "동일" if new_order == base_order else "★ 뒤집힘 ★"
        print(f"순위: 새 {new_order} / 기존 {base_order} → {verdict}")
        print(f"최대 절대오차: {max(abs(a - b) for a, b in zip(new, base)):.4f}")
    else:
        print("(기존 모델이 없어 비교를 건너뜁니다)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fp16", action="store_true", help="fp32로 내보낸 뒤 fp16으로 변환")
    parser.add_argument("--verify", action="store_true", help="기존 int8 모델과 순위 비교")
    parser.add_argument(
        "--baseline", type=Path, default=Path("models/bge-reranker-v2-m3-onnx-int8")
    )
    args = parser.parse_args()

    if args.fp16:
        fp32_dir = args.out.parent / f"{args.out.name}-fp32-tmp"
        export(args.model_id, fp32_dir)
        to_fp16(fp32_dir, args.out)
    else:
        export(args.model_id, args.out)

    if args.verify:
        verify(args.out, args.baseline)


if __name__ == "__main__":
    main()
