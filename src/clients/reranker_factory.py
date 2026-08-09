"""공유 리랭커 클라이언트 — 프로세스 전역 싱글턴 (embedding_factory 패턴 미러).

RerankerClient는 __init__에서 ONNX 세션+토크나이저를 즉시 로드한다(수 초).
프로젝트 실행마다 새로 만들면 로드가 반복되고 모델이 여러 벌 RAM에 올라가므로
get_reranker_client()로 한 인스턴스를 공유한다. 동시성 안전성 논거는
embedding_factory와 동일(단일 프로세스 asyncio, 추론 시 read-only).
"""

from __future__ import annotations

from src.clients.reranker_client import BgeRerankerV2M3Client, RerankerClient

_singleton: RerankerClient | None = None


def get_reranker_client() -> RerankerClient:
    """공유 bge-reranker 클라이언트. 최초 호출 시 모델을 1회 로드한다."""
    global _singleton
    if _singleton is None:
        _singleton = BgeRerankerV2M3Client()
    return _singleton


def reset_reranker_client() -> None:
    """싱글턴 해제 (테스트·재설정용)."""
    global _singleton
    _singleton = None
