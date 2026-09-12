"""Memory backend contract.

ShadowScribe owns *sensation* (audio → transcript → distilled structure). It does
not own *recollection*. That is delegated to a pluggable backend so the same
sensory pipeline can feed `causal-memory` in production and a dependency-free
SQLite store on a laptop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

VALID_RELATIONS = ("caused", "enabled", "prevented", "no_effect")
VALID_CONFIDENCE_SOURCES = ("temporal", "rule", "llm_inferred", "user_feedback")


@dataclass(slots=True)
class CausalEdge:
    """One distilled ``cause → effect`` relationship, attributed to a speaker."""

    cause: str
    effect: str
    relation: str = "caused"
    task_tag: str = "general"
    context: str | None = None
    actor: str = "unknown"
    """``owner`` when the master said/did it, ``other`` when a counterparty did."""

    evidence: str | None = None
    confidence: float = 0.0
    occurred_at: str | None = None
    """ISO-8601 date (``YYYY-MM-DD``); the memory store uses it for recency decay."""

    def normalized_relation(self) -> str:
        rel = (self.relation or "caused").strip().lower().replace("-", "_")
        return rel if rel in VALID_RELATIONS else "caused"


@dataclass(slots=True)
class Fact:
    key: str
    value: str
    scope: str = "user"
    confidence: float = 0.7
    replace_same_key: bool = False


@dataclass(slots=True)
class EpisodeInput:
    """Everything one distillation window contributes to long-term memory."""

    title: str
    summary: str
    edges: list[CausalEdge] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    occurred_at: str | None = None
    session_context: str | None = None


@dataclass(slots=True)
class WriteResult:
    ids: list[str] = field(default_factory=list)
    edges_written: int = 0
    facts_written: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@runtime_checkable
class MemoryBackend(Protocol):
    """What the pipeline and the query API need from a long-term store."""

    name: str

    def write_episode(self, episode: EpisodeInput) -> WriteResult:
        """Persist distilled structure. Must never raise for a single bad edge."""

    def search(self, query: str, limit: int = 10, detail_level: str = "l1") -> str:
        """Relevance search across all memory types, rendered as markdown/text."""

    def directory(self, limit: int = 20) -> str:
        """Compact L0 pointer list — cheap enough to pin in a system prompt."""

    def facts(self, query: str, limit: int = 10) -> str:
        """Flat-fact lookup."""

    def reconstruct(self, query: str) -> str:
        """Narrative reconstruction around a topic, when the backend supports it."""

    def stats(self) -> dict:
        """Backend health/statistics for ``/v1/health`` and ``ss status``."""
