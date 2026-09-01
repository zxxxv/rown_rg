"""공유 리랭커 클라이언트 — 프로세스 전역 싱글턴 (embedding_factory 패턴 미러).

RerankerClient는 __init__에서 ONNX 세션+토크나이저를 즉시 로드한다(수 초).
프로젝트 실행마다 새로 만들면 로드가 반복되고 모델이 여러 벌 RAM에 올라가므로
get_reranker_client()로 한 인스턴스를 공유한다. 동시성 안전성 논거는
embedding_factory와 동일(단일 프로세스 asyncio, 추론 시 read-only).

``settings.reranker_remote_url``이 채워져 있으면 로컬 모델 대신 원격 GPU 서비스로
분기한다. 앱의 나머지는 이 팩토리 하나만 보므로 호출부는 바뀌지 않는다.
"""

from __future__ import annotations

import structlog

from src.clients.reranker_client import BgeRerankerV2M3Client, RerankerClient
from src.core.config import settings

logger = structlog.get_logger(__name__)

_singleton: RerankerClient | None = None


def get_reranker_client() -> RerankerClient:
    """공유 리랭커 클라이언트. 원격 설정이 있으면 원격, 없으면 로컬 모델."""
    global _singleton
    if _singleton is None:
        if settings.reranker_remote_url:
            # 지연 import — 원격을 안 쓰는 배포에서 httpx 경로를 끌어올 이유가 없다.
            from src.clients.remote_reranker_client import RemoteRerankerClient

            logger.info("reranker.factory.remote", url=settings.reranker_remote_url)
            _singleton = RemoteRerankerClient()
        else:
            _singleton = BgeRerankerV2M3Client()
    return _singleton


def peek_reranker_client() -> RerankerClient | None:
    """이미 만들어진 싱글턴만 돌려준다 — 없으면 None (embedding_factory 미러).

    모니터 조회가 무거운 로컬 모델 로드를 유발하지 않게 한다.
    """
    return _singleton


def reset_reranker_client() -> None:
    """싱글턴 해제 (테스트·재설정용)."""
    global _singleton
    _singleton = None
