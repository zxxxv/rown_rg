"""BGE-M3 임베딩 알맹이 — 앱 설정에 의존하지 않는 순수 계층.

``onnx_cross_encoder``와 같은 이유로 존재한다. 임베딩을 원격 GPU 서비스로 옮기면
토크나이즈·배치·풀링·정규화가 앱과 서버 양쪽에 생기는데, 두 벌이 갈라지면 **벡터가
조용히 달라진다**. 리랭커는 점수가 저장되지 않아 그나마 회복이 쉽지만, 임베딩은
그 벡터가 색인에 박힌다 — ``max_length`` 한 자리 차이로 만들어진 벡터가 검색 품질을
갉아먹고, 그것도 에러 없이 그렇게 된다. 그래서 알맹이는 이 파일 한 벌만 둔다.

**계약: 이 파일은 src.core.config·structlog·psutil·DB를 import하지 않는다.**
GPU 서비스 이미지는 ``gpu_service/``와 이 계층만 복사해서 뜨기 때문에, 앱 의존이
하나라도 들어오면 이미지가 깨진다(그쪽에는 DB URL도 LLM 키도 없다). numpy와 표준
라이브러리, 그리고 지연 import하는 onnxruntime·transformers까지가 허용 범위다.

배치 상한을 인자로 받고 스스로 정하지 않는 것도 같은 이유다. 앱은 호스트 RAM에서,
GPU 서비스는 VRAM에서 그 값을 얻어야 하는데 그 판단 근거가 서로 다르다. 여기서는
숫자만 받는다 — ``chars_budget_for_bytes``가 계산을 돕지만 무엇을 넣을지는 호출자
몫이다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from src.clients.onnx_cross_encoder import (
    CPU_PROVIDERS,
    build_session_options,
)

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

__all__ = [
    "DEFAULT_MAX_CHARS_PER_BATCH",
    "DEFAULT_MAX_LENGTH",
    "DIMENSION",
    "MAX_INPUT_CHARS",
    "OnnxTextEmbedder",
    "chars_budget_for_bytes",
    "clamp_input",
    "l2_normalize",
    "make_dynamic_batches",
]

# BGE-M3 dense 벡터 차원. 원격 응답 검증에서 이 값을 확인해야 한다 — 길이만 맞고
# 차원이 다른 응답이 색인에 들어가면 검색이 통째로 어긋난다.
DIMENSION = 1024
# XLM-RoBERTa 최대 입력. 한국어 800자 ≈ 400~500 토큰.
DEFAULT_MAX_LENGTH = 512
# 한 배치의 누적 글자 수 상한 기본값. 이보다 큰 값은 호출자가 명시해야 한다.
DEFAULT_MAX_CHARS_PER_BATCH = 32_000
# 단일 입력의 글자 수 hard cap. 토크나이저는 max_length에서 자르지만 **그 전에**
# fast tokenizer가 빌드하는 BPE/SP 중간 구조가 통째 입력을 메모리에 올린다 -
# 14,000자급 단락에서 OOM이 났다. 한국어 512토큰은 2,000자에 못 미치므로 4,000자면
# 안전 마진 2배다. prefix-stable tokenization이라 결과 벡터는 원본과 같다(모델이
# 보는 첫 512토큰이 동일하다).
MAX_INPUT_CHARS = 4_000


def l2_normalize(x: np.ndarray) -> np.ndarray:
    """행 단위 L2 정규화 — 코사인 유사도 검색용.

    clip은 0-벡터에서의 0 나눗셈 방어다. 이론상 나오지 않지만, 나왔을 때 NaN이
    색인에 들어가면 그 뒤로 유사도 계산이 전부 오염된다.
    """
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.clip(norms, a_min=1e-12, a_max=None)


def clamp_input(texts: list[str], *, max_chars: int = MAX_INPUT_CHARS) -> list[str]:
    """입력 길이 hard cap 적용.

    캐시를 쓰는 호출자는 **캐시 키를 만들기 전에** 이걸 통과시켜야 한다. 안 그러면
    같은 문서가 절단 전/후 두 벌로 캐시되고, 둘이 같은 벡터인데 키가 달라진다.
    """
    return [t[:max_chars] if len(t) > max_chars else t for t in texts]


def chars_budget_for_bytes(total_bytes: int) -> int:
    """가용 메모리(바이트)로부터 배치 글자 수 상한을 고른다.

    앱은 호스트 RAM을, GPU 서비스는 **VRAM 여유분**을 넣는다. 같은 표를 쓰는 이유는
    둘 다 "한 forward의 활성화 텐서가 얼마나 부풀 수 있나"를 재는 것이기 때문이다.
    활성화 크기는 배치 안 최장 시퀀스에 맞춘 패딩으로 결정되므로 글자 수 합이 대리
    지표가 된다.

    경계값은 임의가 아니라 실측에서 왔다: 8GB급에서 32,000자가 중간 텐서 피크 14GB를
    만들어 OOM이 났다(2026-08-12). 그래서 그 아래 등급은 16,000자로 내린다.
    """
    gb = total_bytes / 1024**3
    if gb >= 30:
        return 128_000
    if gb >= 14:
        return 32_000
    return 16_000


def make_dynamic_batches(texts: list[str], max_chars: int) -> list[list[str]]:
    """누적 글자 수가 ``max_chars``를 넘기 전에 배치를 자른다.

    고정 개수로 묶지 않는 이유: 패딩이 배치 안 최장 시퀀스에 맞춰지므로, 짧은 글
    100개와 긴 글 100개의 활성화 메모리가 자릿수로 다르다. 글자 수로 묶으면 그
    부담이 균일해진다.

    혼자서 상한을 넘는 텍스트는 자기 혼자 배치가 된다 — 버리지 않는다. ``clamp_input``이
    이미 4,000자로 잘라 두므로 실제로는 상한을 크게 넘지 않는다.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    total = 0
    for t in texts:
        if current and total + len(t) > max_chars:
            batches.append(current)
            current, total = [], 0
        current.append(t)
        total += len(t)
    if current:
        batches.append(current)
    return batches


class OnnxTextEmbedder:
    """ONNX 텍스트 임베더 한 벌 — 토크나이즈·전방계산·CLS 풀링·정규화.

    동기 API만 노출한다. 스레드로 내보낼지 세마포어로 조일지는 호출자 사정이라
    여기서 정하지 않는다(앱은 배치마다 ``asyncio.to_thread``, GPU 서비스는 요청
    단위 세마포어). ``onnx_cross_encoder.OnnxCrossEncoder``와 같은 규약이다.
    """

    def __init__(
        self,
        model_dir: str | Path,
        *,
        max_length: int = DEFAULT_MAX_LENGTH,
        max_chars_per_batch: int = DEFAULT_MAX_CHARS_PER_BATCH,
        providers: tuple[str, ...] | list[str] = CPU_PROVIDERS,
        intra_op_num_threads: int | None = None,
    ) -> None:
        # 무거운 임포트는 인스턴스 생성 시점까지 미룬다 — 모듈 import만으로
        # onnxruntime·transformers를 끌어오면 단위 테스트 수집 비용이 크게 는다.
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self._model_dir = Path(model_dir)
        self.max_length = max_length
        self.max_chars_per_batch = max_chars_per_batch

        self._session = ort.InferenceSession(
            str(self._model_dir / "model.onnx"),
            sess_options=build_session_options(ort, intra_op_num_threads=intra_op_num_threads),
            providers=list(providers),
        )
        self._tokenizer: PreTrainedTokenizerBase = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
            str(self._model_dir)
        )
        # HF fast tokenizer는 Rust 백엔드라 스레드 안전하지 않다. 동시에 부르면
        # "Already borrowed"로 죽는다(2026-08-12 실사고: 자료 41개 중 4개만 색인되고
        # 파이프라인이 3시간 넘게 멈췄다). 임베딩은 asyncio.to_thread로 나가고 청킹은
        # 이벤트 루프에서 같은 토크나이저를 부르므로 실제로 겹친다. 락을 밖에도
        # 노출하는 이유는 토크나이저를 빌려 쓰는 쪽이 같은 락을 잡아야 직렬화가
        # 성립하기 때문이다. FastAPI도 요청을 동시에 받으므로 GPU 서비스에서도 필요하다.
        self._tokenizer_lock = threading.Lock()

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        """토크나이저 재사용 통로 — 토큰 수 계산 등이 같은 인스턴스를 써야 한다."""
        return self._tokenizer

    @property
    def tokenizer_lock(self) -> threading.Lock:
        """토크나이저 직렬화 락 — 이 토크나이저를 쓰는 모든 곳이 같은 락을 잡아야 한다."""
        return self._tokenizer_lock

    @property
    def providers(self) -> list[str]:
        """실제로 활성화된 실행 공급자 — CUDA 요청이 조용히 CPU로 떨어졌는지 확인용."""
        return list(self._session.get_providers())

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        """한 번의 ONNX 전방계산 — ``(N, DIMENSION)`` L2 정규화 벡터를 돌려준다.

        호출자가 배치 크기를 이미 정해서 넘긴다고 가정한다. 배치 분할은 ``embed``가 한다.
        """
        # 토큰화만 락으로 묶는다 - ONNX 추론은 스레드 안전하고 시간의 대부분을
        # 차지하므로 그것까지 직렬화하면 병렬화가 통째로 무의미해진다.
        with self._tokenizer_lock:
            enc = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
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
        token_embeddings = outputs[0]  # (batch, seq, hidden)
        # BGE-M3 dense는 CLS 풀링이다. mean 풀링으로 바꾸면 벡터 공간이 통째로
        # 달라져 기존 색인과 호환되지 않는다.
        cls = token_embeddings[:, 0, :]
        return l2_normalize(cls).astype(np.float32)

    def embed(self, texts: list[str], *, max_chars_per_batch: int | None = None) -> np.ndarray:
        """입력 전체를 동적 배치로 나눠 임베딩 — 동기. 빈 입력은 ``(0, DIMENSION)``.

        반환 순서는 입력 순서와 1:1이다. 호출자가 그 전제로 청크·벡터를 묶으므로
        여기서 순서를 바꾸면 색인이 조용히 어긋난다.
        """
        if not texts:
            return np.empty((0, DIMENSION), dtype=np.float32)
        budget = max_chars_per_batch or self.max_chars_per_batch
        out: list[np.ndarray] = []
        for batch in make_dynamic_batches(texts, budget):
            out.append(self.encode_batch(batch))
        return np.vstack(out)
