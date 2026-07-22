"""Retrieval layer public API.

Only the contract (:class:`SearchClient`, :class:`SearchHit`) plus the
:class:`HybridSearchClient` facade and :func:`rerank_hits` helper are
exposed. Concrete backends in ``_keyword``, ``_semantic``, ``_reranking``
are module-private — external callers route through the facade.
"""

from src.services.retrieval._hyde import HyDEQueryGenerator, HyDESearchClient
from src.services.retrieval._reranking import rerank_hits
from src.services.retrieval.base import ScoreSource, SearchClient, SearchHit, Track
from src.services.retrieval.hybrid import HybridSearchClient

__all__ = [
    "HyDEQueryGenerator",
    "HyDESearchClient",
    "HybridSearchClient",
    "ScoreSource",
    "SearchClient",
    "SearchHit",
    "Track",
    "rerank_hits",
]
