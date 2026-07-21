"""공유 임베딩 클라이언트 — 프로세스 전역 싱글턴 (llm/factory.py 패턴 미러).

BgeM3Client는 __init__에서 ONNX 세션+토크나이저를 즉시 로드한다(무겁다). 검색·인덱싱이
각자 BgeM3Client()를 만들면 모델이 여러 벌 RAM에 올라간다. get_embedding_client()로 한
인스턴스를 공유해 모델을 1회만 로드한다.

안전성: 단일 프로세스 asyncio에서 None 체크와 대입 사이에 await가 없어 race가 없다.
모델·토크나이저는 추론 시 read-only라 동시 embed 호출에도 안전하다(이벤트루프에서 직렬화됨).
멀티 프로세스(워커 여러 개)면 프로세스마다 1벌 — 프로세스 간 가중치 공유는 불가하며 정상이다.
"""

from __future__ import annotations

from src.clients.embedding_client import BgeM3Client, EmbeddingClient

_singleton: EmbeddingClient | None = None


def get_embedding_client() -> EmbeddingClient:
    """공유 BGE-M3 클라이언트. 최초 호출 시 모델을 1회 로드한다."""
    global _singleton
    if _singleton is None:
        _singleton = BgeM3Client()
    return _singleton


def reset_embedding_client() -> None:
    """싱글턴 해제 (테스트·재설정용)."""
    global _singleton
    _singleton = None
