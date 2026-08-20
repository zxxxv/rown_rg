"""BGE cross-encoder 재채점 알맹이 — 앱 설정에 의존하지 않는 순수 계층.

리랭커를 원격 GPU 서비스로 옮기면 토크나이즈·배치·시그모이드가 앱과 서버 양쪽에
생긴다. 두 벌이 갈라지면 **점수가 조용히 달라진다** — ``max_length`` 한 자리만
어긋나도 패시지 절단 위치가 바뀌어 순위가 바뀌는데, 어디서도 에러가 안 난다.
그래서 알맹이는 이 파일 한 벌만 두고 앱(``src.clients.reranker_client``)과 GPU
서비스(``gpu_service/``)가 똑같이 import한다.

**계약: 이 파일은 src.core.config·structlog·DB를 import하지 않는다.** GPU 서비스
이미지는 이 파일과 ``gpu_service/``만 복사해서 뜨기 때문에, 앱 의존이 하나라도
들어오면 이미지가 깨진다. numpy와 표준 라이브러리, 그리고 지연 import하는
onnxruntime·transformers까지가 허용 범위다.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

# XLM-RoBERTa 최대 입력. 한국어 800자 ≈ 400~500 토큰, 512에서 안전 절단.
DEFAULT_MAX_LENGTH = 512
# cross-encoder는 쿼리당 1배치 latency가 지배 — 너무 큰 배치는 padding 손해.
DEFAULT_BATCH_SIZE = 16
CPU_PROVIDERS = ("CPUExecutionProvider",)
# GPU에서도 CUDA가 없으면 CPU로 떨어지도록 뒤에 CPU를 붙인다(ORT가 앞에서부터 시도).
CUDA_PROVIDERS = ("CUDAExecutionProvider", "CPUExecutionProvider")


def sigmoid(x: np.ndarray) -> np.ndarray:
    """Overflow-safe sigmoid over raw cross-encoder logits."""
    # 안정 변형: 항상 음수 지수만 평가해 overflow를 차단. x≥0이면 1/(1+exp(-x)),
    # x<0이면 exp(x)/(1+exp(x)). np.where는 양쪽 분기를 모두 평가하므로 -|x|로
    # 마스킹한 뒤 분기별 공식으로 합성한다.
    neg_abs = -np.abs(x)
    e = np.exp(neg_abs)
    return np.where(x >= 0, 1.0 / (1.0 + e), e / (1.0 + e))


def build_session_options(ort: Any, *, intra_op_num_threads: int | None = None) -> Any:
    """ONNX Runtime 세션 옵션 — CPU 메모리 아레나를 끈다.

    기본 아레나 할당자는 한 번 커지면 **OS에 돌려주지 않는다**. 배치 안 최장 시퀀스에
    맞춘 패딩으로 중간 텐서가 부풀면(실측 피크 14GB) 그 크기를 프로세스가 계속 붙든다.
    실제로 운영 서버가 CPU 0.2% idle 상태에서 29.4GB/31GB를 점유했다(2026-08-12).
    런이 죽어도 안 줄어들어 다음 런이 OOM으로 죽는다.

    아레나를 끄면 할당마다 malloc/free가 돌아 추론이 조금 느려지지만, 우리는 요청당
    수백 ms 단위 추론을 하루 수천 번 도는 게 아니라 배치 색인을 가끔 돈다 - 메모리를
    돌려받는 쪽이 훨씬 값어치 있다.

    Args:
        ort: 호출자가 이미 import한 ``onnxruntime`` 모듈.
        intra_op_num_threads: None이면 코어 수 - 1. 코어 하나는 이벤트 루프 몫으로
            남긴다(추론이 전 코어를 잡으면 색인·리랭킹 도는 내내 API·WS가 굳는다,
            2026-08-13). GPU 실행에서는 대부분 무의미하지만 CPU 폴백에 그대로 쓰인다.
    """
    opts = ort.SessionOptions()
    opts.enable_cpu_mem_arena = False
    opts.intra_op_num_threads = intra_op_num_threads or max(1, (os.cpu_count() or 2) - 1)
    return opts


class OnnxCrossEncoder:
    """ONNX cross-encoder 한 벌 — 토크나이즈·전방계산·시그모이드.

    동기(sync) API만 노출한다. 스레드로 내보낼지, 세마포어로 조일지는 호출자
    사정이라 여기서 정하지 않는다(앱은 배치마다 ``asyncio.to_thread``, GPU 서비스는
    요청 단위 세마포어).
    """

    def __init__(
        self,
        model_dir: str | Path,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_length: int = DEFAULT_MAX_LENGTH,
        providers: tuple[str, ...] | list[str] = CPU_PROVIDERS,
        intra_op_num_threads: int | None = None,
    ) -> None:
        # 무거운 임포트는 인스턴스 생성 시점까지 미룸 — 모듈 import만으로 onnxruntime·
        # transformers를 끌어오면 단위 테스트 수집 비용이 크게 늘어남.
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self._model_dir = Path(model_dir)
        self.batch_size = batch_size
        self.max_length = max_length

        self._session = ort.InferenceSession(
            str(self._model_dir / "model.onnx"),
            sess_options=build_session_options(ort, intra_op_num_threads=intra_op_num_threads),
            providers=list(providers),
        )
        self._tokenizer: PreTrainedTokenizerBase = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
            str(self._model_dir)
        )
        # 리랭킹은 asyncio.to_thread로 나가고 절 단위 병렬 작성이 여러 개를 동시에
        # 부른다. HF fast tokenizer는 Rust 백엔드라 동시 호출 시 "Already borrowed"로
        # 죽는다(2026-08-12 색인에서 실제로 터졌다). 토큰화만 직렬화한다 - ONNX 추론은
        # 스레드 안전하고 시간의 대부분이라 그것까지 묶으면 병렬화가 무의미해진다.
        # FastAPI도 요청을 동시에 받으므로 GPU 서비스에서도 같은 락이 필요하다.
        self._tokenizer_lock = threading.Lock()

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        """Expose the HF tokenizer for downstream reuse (e.g. token counting)."""
        return self._tokenizer

    @property
    def providers(self) -> list[str]:
        """실제로 활성화된 실행 공급자 — CUDA 요청이 조용히 CPU로 떨어졌는지 확인용."""
        return list(self._session.get_providers())

    def score_batch(self, query: str, passages: list[str]) -> np.ndarray:
        """Run one ONNX forward pass over ``(query, passages)`` pairs.

        Returns:
            1-D float32 array of length ``len(passages)`` — raw logits
            (no sigmoid applied).
        """
        queries = [query] * len(passages)
        with self._tokenizer_lock:
            enc = self._tokenizer(
                queries,
                passages,
                padding=True,
                # passage만 자르고 query는 보존 — cross-encoder에서 query 절단은 의미 손상이 크다.
                truncation="only_second",
                max_length=self.max_length,
                return_tensors="np",
            )
        outputs = self._session.run(
            None,
            {
                "input_ids": enc["input_ids"].astype(np.int64),
                "attention_mask": enc["attention_mask"].astype(np.int64),
            },
        )
        # logits shape: (batch, 1) → (batch,)
        logits: np.ndarray = outputs[0].squeeze(-1).astype(np.float32)
        return logits

    def score(
        self, query: str, passages: list[str], *, batch_size: int | None = None
    ) -> list[float]:
        """모든 패시지를 배치로 나눠 채점 — 동기. 빈 입력은 ``[]``."""
        if not passages:
            return []
        size = batch_size or self.batch_size
        scores: list[float] = []
        for start in range(0, len(passages), size):
            logits = self.score_batch(query, passages[start : start + size])
            scores.extend(sigmoid(logits).tolist())
        return scores
