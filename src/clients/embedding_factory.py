"""공유 임베딩 클라이언트 — 프로세스 전역 싱글턴 (llm/factory.py 패턴 미러).

BgeM3Client는 __init__에서 ONNX 세션+토크나이저를 즉시 로드한다(무겁다). 검색·인덱싱이
각자 BgeM3Client()를 만들면 모델이 여러 벌 RAM에 올라간다. get_embedding_client()로 한
인스턴스를 공유해 모델을 1회만 로드한다.

안전성: 단일 프로세스 asyncio에서 None 체크와 대입 사이에 await가 없어 race가 없다.
모델·토크나이저는 추론 시 read-only라 동시 embed 호출에도 안전하다(이벤트루프에서 직렬화됨).
멀티 프로세스(워커 여러 개)면 프로세스마다 1벌 — 프로세스 간 가중치 공유는 불가하며 정상이다.

``settings.embedding_remote_url``이 채워져 있으면 로컬 모델 대신 원격 GPU 서비스로
분기한다(``reranker_factory``와 같은 구조). 앱의 나머지는 이 팩토리 하나만 보므로
호출부는 바뀌지 않는다.

**원격으로 바꾸기 전에 전량 재색인이 끝나 있어야 한다.** 리랭커와 달리 임베딩 벡터는
색인에 저장되므로, 원격(fp16)과 기존 색인(CPU int8)이 다른 공간이면 검색이 조용히
나빠진다. 자세한 배경은 ``remote_embedding_client`` 모듈 docstring 참조.
"""

from __future__ import annotations

import structlog

from src.clients.embedding_client import BgeM3Client, EmbeddingClient
from src.core.config import settings

logger = structlog.get_logger(__name__)

_singleton: EmbeddingClient | None = None


def get_embedding_client() -> EmbeddingClient:
    """공유 임베딩 클라이언트. 원격 설정이 있으면 원격, 없으면 로컬 모델."""
    global _singleton
    if _singleton is None:
        if settings.embedding_remote_url:
            # 지연 import — 원격을 안 쓰는 배포에서 httpx 경로를 끌어올 이유가 없다.
            from src.clients.remote_embedding_client import RemoteEmbeddingClient

            logger.info("embedding.factory.remote", url=settings.embedding_remote_url)
            _singleton = RemoteEmbeddingClient()
        else:
            _singleton = BgeM3Client()
    return _singleton


def reset_embedding_client() -> None:
    """싱글턴 해제 (테스트·재설정용)."""
    global _singleton
    _singleton = None
