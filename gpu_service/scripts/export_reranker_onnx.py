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
from pathlib import Path

# fp16 변환은 임베딩 내보내기와 공유한다 - 2GB protobuf 우회를 두 벌로 두면
# 한쪽만 고쳐지고 다른 쪽에서 같은 실패가 되돌아온다.
from gpu_service.scripts.onnx_fp16 import to_fp16

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
