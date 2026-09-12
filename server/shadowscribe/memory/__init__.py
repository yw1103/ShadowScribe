"""Memory backend factory.

``SS_MEMORY_BACKEND=causal-memory`` (default) prefers the real causal graph and
degrades to the built-in SQLite store when the bindings are missing, so a fresh
clone never fails to boot just because a Rust wheel did not install.
"""

from __future__ import annotations

import logging

from .base import (
    VALID_RELATIONS,
    CausalEdge,
    EpisodeInput,
    Fact,
    MemoryBackend,
    WriteResult,
)

log = logging.getLogger(__name__)

__all__ = [
    "VALID_RELATIONS",
    "CausalEdge",
    "EpisodeInput",
    "Fact",
    "MemoryBackend",
    "WriteResult",
    "build_backend",
    "get_backend",
]

_backend: MemoryBackend | None = None


def build_backend(settings=None) -> MemoryBackend:
    if settings is None:
        from ..config import settings as settings  # type: ignore[no-redef]

    if settings.memory_backend == "causal-memory":
        try:
            from .causal_memory_backend import CausalMemoryBackend

            return CausalMemoryBackend(
                settings.resolved_memory_db(),
                llm_base_url=settings.llm_base_url,
                llm_api_key=settings.llm_api_key,
                llm_model=settings.llm_model,
            )
        except Exception as exc:  # ImportError, or a store that refuses to open
            log.warning(
                "causal-memory backend unavailable (%s: %s); falling back to native SQLite",
                type(exc).__name__,
                exc,
            )

    from .native import NativeMemoryBackend

    return NativeMemoryBackend(settings.db_path)


def get_backend(settings=None) -> MemoryBackend:
    """Process-wide singleton — the causal-memory store is expensive to open."""
    global _backend
    if _backend is None:
        _backend = build_backend(settings)
    return _backend


def reset_backend() -> None:
    """Test hook."""
    global _backend
    _backend = None
