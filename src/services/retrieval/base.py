"""Retrieval primitives — ABC + result hit shared by all backends.

Backends in this package (``_keyword``, later ``_semantic``, ``_hybrid``) all
return :class:`SearchHit`. ``score_source`` tags the origin so a downstream
fuser can branch on backend-specific score semantics (BM25 vs cosine vs RRF).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ScoreSource = Literal["keyword", "semantic", "hybrid"]
Track = Literal["content", "style"]


class SearchHit(BaseModel):
    """One retrieved chunk plus its score and source backend tag."""

    chunk_id: UUID
    content: str
    source_id: UUID
    chunk_index: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float
    score_source: ScoreSource


class SearchClient(ABC):
    """Abstract retrieval client.

    Implementations must scope results to ``project_id`` and ``track`` —
    cross-project leakage is a privacy bug, and content/style chunks live
    in the same table but are never combined at the retrieval layer.
    """

    @abstractmethod
    async def search(
        self,
        query: str,
        project_id: UUID,
        track: Track = "content",
        top_k: int = 50,
    ) -> list[SearchHit]:
        """Return up to ``top_k`` hits sorted by descending score."""
