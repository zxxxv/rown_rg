"""Text embedding clients.

Provides an abstract :class:`EmbeddingClient` interface, a disk-backed cache,
and a concrete :class:`BgeM3Client` adapter backed by an ONNX INT8 model.
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import os
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import psutil
import structlog
from pydantic import BaseModel

from src.core.config import settings

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

logger = structlog.get_logger(__name__)


def _session_options(ort):
    """ONNX Runtime 세션 옵션 — CPU 메모리 아레나를 끈다.

    기본 아레나 할당자는 한 번 커지면 **OS에 돌려주지 않는다**. 배치 안 최장 시퀀스에
    맞춘 패딩으로 중간 텐서가 부풀면(실측 피크 14GB) 그 크기를 프로세스가 계속 붙든다.
    실제로 운영 서버가 CPU 0.2% idle 상태에서 29.4GB/31GB를 점유했다(2026-08-12).
    런이 죽어도 안 줄어들어 다음 런이 OOM으로 죽는다.

    아레나를 끄면 할당마다 malloc/free가 돌아 추론이 조금 느려지지만, 우리는 요청당
    수백 ms 단위 추론을 하루 수천 번 도는 게 아니라 배치 색인을 가끔 돈다 - 메모리를
    돌려받는 쪽이 훨씬 값어치 있다.
    """
    opts = ort.SessionOptions()
    opts.enable_cpu_mem_arena = False
    # 코어 하나는 이벤트 루프 몫으로 남긴다. 기본값(전체 코어)이면 추론이 CPU를 다
    # 잡아 색인 도는 내내 API·WS가 통째로 굳는다(2026-08-13 사용자 보고 - 임베딩
    # 시작하면 웹 끊김. 운영 2vCPU에서 특히 치명). 추론은 그만큼 느려지지만 색인은
    # 배경 작업이고, 화면이 죽는 것보다 낫다.
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
    return opts


class EmbeddingResult(BaseModel):
    """One embedded text plus its provenance.

    Attributes:
        embedding: Dense vector, length equal to the client's ``DIMENSION``.
        text: Original input text, preserved so callers can route results
            without separately tracking input order.
        cached: True if the embedding was loaded from the disk cache,
            False if it was just computed.
    """

    embedding: list[float]
    text: str
    cached: bool


class EmbeddingClient(ABC):
    """Abstract embedding client.

    Subclasses must declare ``DIMENSION`` (output vector size) and implement
    both :meth:`embed` and :meth:`embed_batch`. ``embed_batch`` exists as a
    separate abstract method (not a default fan-out over :meth:`embed`) so
    each backend can group cache misses into a single forward pass.
    """

    DIMENSION: ClassVar[int]

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResult:
        """Embed a single text."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed multiple texts, preserving input order."""


class EmbeddingCache:
    """Disk cache for embeddings, keyed by SHA-256 of the input text.

    Files are sharded into 256 subdirectories using the first two hex chars
    of the key, so a single directory does not accumulate millions of files
    (which would slow down ext4 listings and inflate inode usage).
    """

    def __init__(self, root: Path | str = "./cache/embeddings") -> None:
        self.root = Path(root)

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.npy"

    def load(self, text: str) -> np.ndarray | None:
        """Return the cached embedding for ``text``, or None on miss/error."""
        path = self._path_for(self._key(text))
        if not path.exists():
            return None
        try:
            return np.load(path)
        except Exception as e:
            # 손상된 캐시 파일을 만나도 호출자는 정상 재계산으로 이어져야 하므로 None 반환.
            logger.warning(
                "embedding.cache.load_failed",
                path=str(path),
                error_type=type(e).__name__,
            )
            return None

    def store(self, text: str, embedding: np.ndarray) -> None:
        """Persist ``embedding`` for ``text`` as float32 .npy."""
        path = self._path_for(self._key(text))
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, embedding.astype(np.float32))


class BgeM3Client(EmbeddingClient):
    """BGE-M3 ONNX INT8 embedding client.

    Uses CLS pooling on ``token_embeddings`` plus L2 normalization, matching
    the configuration validated in ``reports/bge_m3_setup.md``
    (INT8 vs PyTorch mean cosine = 0.9804 on 10 Korean samples).
    The ONNX graph also exposes a fused ``sentence_embedding`` output, but
    we read ``token_embeddings`` and pool ourselves to keep the pipeline
    identical to the setup script — switching the pooling source must be
    re-validated against the report's accuracy threshold before deployment.
    """

    # BGE-M3 출력 차원, 변경 불가 (모델 가중치가 1024-d로 학습됨)
    DIMENSION: ClassVar[int] = 1024
    # BGE-M3는 8192까지 가능하나 메모리·지연 절감 위해 512 기본. 긴 문서는 분할 후 임베딩.
    MAX_LENGTH: ClassVar[int] = 512
    # mean pooling 사용 시 한국어 정확도 약 -1.5% (사전 실험 결과). 변경 시 재검증 필수.
    POOLING: ClassVar[str] = "cls"
    # 16GB RAM 기준 한 forward pass 누적 글자 수 상한. 14000자급 단락 32개를 한 배치에
    # 묶으면 ONNX 활성화 텐서가 RAM을 폭주시켜 OOM이 난다. 동적 배치는 글자 수 합이 이
    # 한도를 넘기 전에 배치를 분할해 메모리 부담을 균일화한다.
    MAX_CHARS_PER_BATCH: ClassVar[int] = 32_000
    # 단일 입력 텍스트의 글자 수 hard cap. tokenizer는 max_length=512 토큰에서 자르지만
    # 그 이전에 fast tokenizer가 빌드하는 BPE/SP 중간 구조가 통째 입력을 RAM에 올려
    # 14000자급 단락에서 OOM이 발생한다. 한국어 기준 512토큰은 ~2000자에 못 미치므로
    # 4000자 cap은 안전 margin 2배 — prefix-stable tokenization 특성상 결과 임베딩은
    # 원본과 동일하다 (모델이 보는 첫 512토큰이 같음).
    MAX_INPUT_CHARS: ClassVar[int] = 4_000
    # 가용 메모리 사용률이 이 % 초과 시 배치 사이에 강제 GC. ONNX runtime이 직전 텐서를
    # 늦게 풀 때 누적 OOM을 막는 안전망. swap 4GB 환경에서 80%는 이미 thrash 직전이라
    # 더 일찍 발동.
    MEMORY_PRESSURE_THRESHOLD: ClassVar[int] = 70
    # max_chars_per_batch override의 안전 상한 배수. 자동값을 초과하는 override는 거절 —
    # 자동값과 hard cap 사이를 통과시키면 한 forward에서 폭주하는 batch가 chunk-사이
    # watchdog보다 먼저 OS OOM kill을 부를 수 있다. 자동값보다 큰 값을 시험하려면
    # 자동값 자체가 더 큰(=더 큰 RAM) 호스트로 옮긴다.
    MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER: ClassVar[float] = 1.0

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        device: str = "cpu",
        cache: EmbeddingCache | None = None,
        max_chars_per_batch: int | None = None,
        max_length: int | None = None,
    ) -> None:
        """Initialize the BGE-M3 client.

        Args:
            model_path: Directory containing ``model.onnx`` plus tokenizer
                files. Defaults to ``settings.embedding_model_path``.
            device: ``"cpu"`` (default, AVX-VNNI INT8 path) or ``"cuda"``
                (FP16 model on Turing GPUs, INT8 on Tensor-Core GPUs). The
                caller is responsible for pointing ``model_path`` at the
                matching quantization directory; this flag only picks the
                ONNX Runtime providers list.
            cache: Disk cache instance. Defaults to a new
                :class:`EmbeddingCache` rooted at ``settings.embedding_cache_dir``.
            max_chars_per_batch: Override the RAM-derived dynamic batching
                cap. Use for memory-constrained tests or to force a specific
                batch granularity. When None, the cap is auto-tuned from the
                host's total RAM.
            max_length: Override tokenizer truncation length.
        """
        # 무거운 외부 임포트는 인스턴스 생성 시점까지 미룸 — 모듈 import만으로
        # onnxruntime·transformers를 끌어오면 단위 테스트 콜렉션 비용이 크게 늘어남.
        import onnxruntime as ort
        from transformers import AutoTokenizer

        resolved_path = Path(model_path or settings.embedding_model_path)
        self._model_dir = resolved_path
        self._device = device
        self._cache = cache or EmbeddingCache(root=settings.embedding_cache_dir)
        self._max_length = max_length or self.MAX_LENGTH

        # 호스트 RAM에 따라 동적 배치 상한 자동 조정. 외부에서도 같은 값을 참조할 수
        # 있도록 staticmethod로 노출.
        auto_max_chars = self.auto_max_chars_per_batch()

        # override가 자동 안전값의 MULTIPLIER 배를 넘으면 거절. silent clip 대신 raise —
        # 호출자(특히 벤치마크 sweep)는 자기가 설정한 값이 측정에 그대로 들어간다고 가정하기
        # 때문에 조용히 clip하면 결과가 왜곡되고 같은 OOM이 다른 경로로 돌아온다.
        if max_chars_per_batch is not None:
            hard_cap = int(auto_max_chars * self.MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER)
            if max_chars_per_batch > hard_cap:
                total_ram_gb = psutil.virtual_memory().total / 1024**3
                raise ValueError(
                    f"max_chars_per_batch={max_chars_per_batch} exceeds hard cap "
                    f"{hard_cap} (= auto {auto_max_chars} × "
                    f"{self.MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER}). "
                    f"Total RAM={total_ram_gb:.1f}GB. Lower the override or measure "
                    f"on a larger host."
                )
            self._max_chars_per_batch = max_chars_per_batch
        else:
            self._max_chars_per_batch = auto_max_chars

        logger.info(
            "embedding.client.init.started",
            model_path=str(resolved_path),
            max_chars_per_batch=self._max_chars_per_batch,
            max_length=self._max_length,
        )
        # cuda를 요청하면 CUDA EP를 먼저, 실패 시 CPU EP로 ORT가 자동 폴백 — Turing은
        # libcublasLt.so.12 등 cu12 런타임 wheel(`nvidia-cublas-cu12` 등)이 LD_LIBRARY_PATH
        # 에 있어야 실제로 GPU에 올라간다.
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if device == "cuda"
            else ["CPUExecutionProvider"]
        )
        t0 = time.perf_counter()
        self._session = ort.InferenceSession(
            str(resolved_path / "model.onnx"),
            sess_options=_session_options(ort),
            providers=providers,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(resolved_path))
        # HF fast tokenizer는 Rust 백엔드라 스레드 안전하지 않다. 여러 스레드에서 동시에
        # 부르면 "RuntimeError: Already borrowed"로 죽는다(2026-08-12 실전 런: 자료 41개
        # 중 4개만 색인되고 파이프라인이 3시간 넘게 멈췄다). 임베딩은 asyncio.to_thread로
        # 나가고 청킹은 이벤트 루프에서 같은 토크나이저를 부르므로 실제로 겹친다.
        # 락을 클라이언트가 소유하고 밖에도 노출한다 - 토크나이저를 빌려 쓰는 쪽
        # (services/indexing/_chunking)이 같은 락을 잡아야 직렬화가 성립한다.
        self._tokenizer_lock = threading.Lock()
        logger.info(
            "embedding.client.init.completed",
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )

    @property
    def tokenizer_lock(self) -> threading.Lock:
        """토크나이저 직렬화 락 — 이 토크나이저를 쓰는 모든 곳이 같은 락을 잡아야 한다."""
        return self._tokenizer_lock

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        """Expose the HF tokenizer for downstream reuse (e.g. token counting).

        Returning the same instance avoids paying the from_pretrained cost
        twice and ensures truncation/special-token handling stays consistent
        across embedding and any tokenizer-based estimators.
        """
        return self._tokenizer

    @staticmethod
    def auto_max_chars_per_batch() -> int:
        """Return the host-RAM-based default for ``max_chars_per_batch``.

        Three tiers: ≥30 GB hosts get a high throughput setting, 14-30 GB
        hosts (typical workstation) get the safe mid value, and smaller
        hosts (CI containers, low-RAM laptops) get a conservative one.

        Exposed as a staticmethod so callers (e.g. benchmark sweep
        definition) can scale their own parameters to the host without
        duplicating the tier table.
        """
        total_ram_gb = psutil.virtual_memory().total / 1024**3
        if total_ram_gb >= 30:
            return 128_000
        if total_ram_gb >= 14:
            return BgeM3Client.MAX_CHARS_PER_BATCH
        return 16_000

    @staticmethod
    def _make_dynamic_batches(texts: list[str], max_chars: int) -> list[list[str]]:
        """Group texts into batches whose accumulated char count stays under ``max_chars``.

        A single text exceeding ``max_chars`` becomes its own (oversized) batch —
        tokenizer truncation caps the eventual tensor regardless, so we never
        raise to the caller.

        Args:
            texts: Texts to batch, in the order they should be processed.
            max_chars: Per-batch accumulated character ceiling. Adding a text
                that would push the sum over this value starts a new batch
                (unless the current batch is empty).

        Returns:
            List of batches; each batch is a non-empty list of texts.
        """
        if not texts:
            return []
        batches: list[list[str]] = []
        current: list[str] = []
        current_chars = 0
        for text in texts:
            if current and current_chars + len(text) > max_chars:
                batches.append(current)
                current = []
                current_chars = 0
            current.append(text)
            current_chars += len(text)
        if current:
            batches.append(current)
        return batches

    async def embed(self, text: str) -> EmbeddingResult:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(
        self,
        texts: list[str],
        *,
        sort_for_padding: bool = True,
    ) -> list[EmbeddingResult]:
        """Embed each text, hitting the disk cache when possible.

        Cache misses are sorted by length (when ``sort_for_padding`` is True),
        grouped into dynamic batches whose accumulated character count stays
        under ``self._max_chars_per_batch``, and sent through ONNX one batch
        per forward pass. Results are returned in the original input order.

        Args:
            texts: Input texts to embed.
            sort_for_padding: When True (default), cache-miss texts are sorted
                by character length before batching so each batch has near-
                uniform sequence lengths. BGE-M3 pads every sequence to the
                batch's longest, so mixed-length inputs make short sequences
                pay for long ones. Disable only to A/B against the sorted path.

        Returns:
            Embeddings in the original input order regardless of
            ``sort_for_padding`` — internal reordering is unwound before
            returning so callers never see permutation.
        """
        if not texts:
            return []

        # OOM 안전 처방: 단일 입력의 글자 수를 모델 단계 이전에 차단. 동일 임베딩이
        # 보장되므로 (MAX_INPUT_CHARS 주석 참조) 캐시 키와 tokenizer 입력 양쪽에 사전
        # cut된 문자열을 사용. EmbeddingResult.text는 호출자가 준 원본을 그대로 보존.
        safe_texts = [
            t[: self.MAX_INPUT_CHARS] if len(t) > self.MAX_INPUT_CHARS else t for t in texts
        ]
        n_truncated = sum(
            1 for orig, safe in zip(texts, safe_texts, strict=True) if orig is not safe
        )

        results: list[EmbeddingResult | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        for i, (orig, safe) in enumerate(zip(texts, safe_texts, strict=True)):
            cached_vec = self._cache.load(safe)
            if cached_vec is not None:
                results[i] = EmbeddingResult(
                    embedding=cached_vec.reshape(-1).tolist(),
                    text=orig,
                    cached=True,
                )
            else:
                miss_indices.append(i)
                miss_texts.append(safe)

        hits = len(texts) - len(miss_texts)
        logger.info(
            "embedding.encode.started",
            n_total=len(texts),
            n_cache_hits=hits,
            n_cache_misses=len(miss_texts),
            n_truncated=n_truncated,
            sort_for_padding=sort_for_padding,
            max_chars_per_batch=self._max_chars_per_batch,
        )

        if miss_texts:
            # 정렬 활성 시 미스 텍스트만 글자 수 오름차순으로 재배열한 뒤 encode.
            # miss_indices도 같은 permutation으로 변환해 결과 슬롯 매핑이 깨지지 않게 한다.
            if sort_for_padding:
                order = sorted(range(len(miss_texts)), key=lambda i: len(miss_texts[i]))
                miss_texts = [miss_texts[i] for i in order]
                miss_indices = [miss_indices[i] for i in order]

            batches = self._make_dynamic_batches(miss_texts, self._max_chars_per_batch)
            t0 = time.perf_counter()
            cursor = 0
            for batch_idx, chunk in enumerate(batches):
                # 메모리 압력이 임계 초과 시 강제 GC. ONNX runtime이 직전 forward 텐서를 늦게
                # 푸는 패턴에서 누적 OOM을 막는 안전망.
                mem_percent = psutil.virtual_memory().percent
                if mem_percent > self.MEMORY_PRESSURE_THRESHOLD:
                    logger.warning(
                        "embedding.memory.pressure",
                        batch_idx=batch_idx,
                        total_batches=len(batches),
                        mem_percent=mem_percent,
                    )
                    gc.collect()

                # 동기 ONNX 추론을 워커 스레드로 — 이벤트 루프에서 직접 돌리면 색인
                # 내내 API 전체가 얼어붙는다(2026-08-04 실측: 개요 조회 무응답).
                # ONNX runtime은 추론 중 GIL을 놓으므로 스레드 오프로드가 유효하다.
                vectors = await asyncio.to_thread(self._encode, chunk)
                for k, safe_text in enumerate(chunk):
                    vec = vectors[k]
                    # 캐시 키는 안전 cut된 텍스트 — 원본과 동일 임베딩이 보장되므로 cut 기준
                    # 으로 캐시해 prefix가 같은 입력은 한 슬롯에 모인다.
                    self._cache.store(safe_text, vec)
                    idx = miss_indices[cursor + k]
                    results[idx] = EmbeddingResult(
                        embedding=vec.tolist(),
                        text=texts[idx],
                        cached=False,
                    )
                cursor += len(chunk)
            logger.info(
                "embedding.encode.completed",
                n_encoded=len(miss_texts),
                n_batches=len(batches),
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

        # 위 두 분기를 거치면 results의 모든 슬롯이 채워짐 — None 잔존 시 invariant 위반.
        assert all(r is not None for r in results), "embed_batch left a slot unfilled"
        return [r for r in results if r is not None]

    def _encode(self, texts: list[str]) -> np.ndarray:
        """Run one ONNX forward pass and return (N, DIMENSION) L2-normalized vectors."""
        # 토큰화만 락으로 묶는다 - ONNX 추론은 스레드 안전하고 시간의 대부분을 차지하므로
        # 그것까지 직렬화하면 병렬화가 통째로 무의미해진다.
        with self._tokenizer_lock:
            enc = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                return_tensors="np",
                max_length=self._max_length,
            )
        outputs = self._session.run(
            None,
            {
                "input_ids": enc["input_ids"].astype(np.int64),
                "attention_mask": enc["attention_mask"].astype(np.int64),
            },
        )
        token_embeddings = outputs[0]  # (batch, seq, hidden)
        cls = token_embeddings[:, 0, :]
        # 코사인 유사도 검색용 — clip으로 0-벡터(이론상 발생 안 함)에서 ZeroDivisionError 방어.
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        normalized = cls / np.clip(norms, a_min=1e-12, a_max=None)
        return normalized.astype(np.float32)
