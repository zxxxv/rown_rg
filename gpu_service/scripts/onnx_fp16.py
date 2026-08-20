"""fp32 ONNX 그래프를 fp16으로 — 리랭커·임베딩 내보내기가 함께 쓴다.

두 벌로 복사하지 않는 이유: 아래 2GB 우회는 한 번 밟아 본 뒤에야 알게 되는 것이라
한쪽만 고쳐지면 다른 쪽에서 같은 실패가 되돌아온다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# 내보낸 폴더에서 같이 옮겨야 하는 파일들. tokenizer.json이 빠지면 컨테이너가
# HF_HUB_OFFLINE=1이라 즉시 실패한다.
SIDECAR_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "config.json",
)


def to_fp16(src_dir: Path, out_dir: Path) -> None:
    """fp32 그래프를 fp16으로 변환 — 가중치 절반, 텐서코어 사용.

    수치가 바뀌므로 순위/유사도를 반드시 대조하고 쓸 것.
    """
    import onnx

    try:
        from onnxruntime.transformers.float16 import convert_float_to_float16
    except ImportError:  # onnxruntime 버전에 따라 위치가 다르다
        from onnxconverter_common.float16 import convert_float_to_float16

    out_dir.mkdir(parents=True, exist_ok=True)

    # 2GB 넘는 모델은 여기서 걸린다. convert_float_to_float16이 내부에서
    # onnx.shape_inference.infer_shapes(model)을 부르는데, 그게 SerializeToString()을
    # 하고 protobuf는 메시지 하나가 2GB를 못 넘는다. bge 계열 fp32는 2.27GB라
    # EncodeError로 죽는다(2026-08-20). 파일 기반 infer_shapes_path는 그 한계가 없으므로
    # 먼저 돌려 두고, 변환기에는 다시 추론하지 말라고 알린다 - 추론을 생략하는 게 아니라
    # 앞당기는 것이라 변환 품질은 그대로다.
    inferred = src_dir / "_shape_inferred.tmp.onnx"
    try:
        # 출력을 src_dir에 두는 이유: 외부 데이터(model.onnx_data) 참조가 상대 경로라
        # 다른 디렉터리에 쓰면 이어서 load할 때 가중치를 못 찾는다.
        onnx.shape_inference.infer_shapes_path(str(src_dir / "model.onnx"), str(inferred))
        model = onnx.load(str(inferred))
    finally:
        inferred.unlink(missing_ok=True)

    # keep_io_types: 입출력은 fp32로 남긴다 - 호출부(int64 입력, float 출력)를 안 바꾸려고.
    converted = convert_float_to_float16(model, keep_io_types=True, disable_shape_infer=True)
    # fp16은 절반이라 2GB 아래로 떨어져 단일 파일로 저장된다(외부 데이터 불필요).
    onnx.save(converted, str(out_dir / "model.onnx"))

    for name in SIDECAR_FILES:
        src = src_dir / name
        if src.exists():
            shutil.copy2(src, out_dir / name)
    print(f"fp16 변환 완료: {out_dir}")
