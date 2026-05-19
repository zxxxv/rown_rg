"""Retrieval layer public API.

Only the contract (:class:`SearchClient`, :class:`SearchHit`) is exposed.
Concrete backends live in ``_keyword``, ``_semantic``, ``_hybrid`` and are
module-private — callers route through the hybrid facade added in 작업 11.
"""

from src.services.retrieval.base import ScoreSource, SearchClient, SearchHit, Track

__all__ = ["ScoreSource", "SearchClient", "SearchHit", "Track"]
