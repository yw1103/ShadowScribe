"""`causal-memory` backend.

Wraps the PyO3 bindings of https://github.com/JingxuanC/causal-memory, which owns
the causal graph, hippocampus-style spreading activation, consolidation and the
desktop-side MCP surface. ShadowScribe only feeds it and reads it back.

The binding object is not documented as thread-safe, and uvicorn serves requests
from a threadpool, so every call is serialised behind one lock. Distillation is
off the hot path, so serialising is cheap insurance.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .base import CausalEdge, EpisodeInput, WriteResult

log = logging.getLogger(__name__)

#: Relation names understood by causal-memory's typed edge model.
_RELATION_MAP = {
    "caused": "caused",
    "enabled": "enabled",
    "prevented": "prevented",
    "no_effect": "no_effect",
}

#: Our actor labels → the short context prefix causal-memory shows in listings.
_ACTOR_PREFIX = {"owner": "【我】", "other": "【对方】", "unknown": ""}


class CausalMemoryBackend:
    name = "causal-memory"

    def __init__(
        self,
        db_path: Path,
        *,
        llm_base_url: str = "",
        llm_api_key: str = "",
        llm_model: str = "",
        embed_base_url: str = "",
        embed_api_key: str = "",
        embed_model: str = "",
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._configure_env(
            llm_base_url, llm_api_key, llm_model, embed_base_url, embed_api_key, embed_model
        )
        self._mem = self._open()

    # ------------------------------------------------------------------ setup
    def _configure_env(
        self,
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
        embed_base_url: str,
        embed_api_key: str,
        embed_model: str,
    ) -> None:
        """Push our own LLM/embedding config into causal-memory's env contract.

        causal-memory reads these once when it builds its store, so they must be
        set before the first ``CausalMemory(...)`` in this process.
        """
        if llm_api_key:
            os.environ.setdefault("CAUSAL_MEMORY_LLM_API", llm_base_url)
            os.environ.setdefault("CAUSAL_MEMORY_LLM_KEY", llm_api_key)
            os.environ.setdefault("CAUSAL_MEMORY_LLM_MODEL", llm_model)
        if embed_api_key:
            os.environ.setdefault("CAUSAL_MEMORY_EMBED_API", embed_base_url)
            os.environ.setdefault("CAUSAL_MEMORY_EMBED_KEY", embed_api_key)
            os.environ.setdefault("CAUSAL_MEMORY_EMBED_MODEL", embed_model)
        os.environ.setdefault("CAUSAL_MEMORY_DB", str(self.db_path))

    def _open(self):
        from causal_memory import CausalMemory

        mem = CausalMemory(str(self.db_path))
        log.info("causal-memory store ready at %s", self.db_path)
        return mem

    # ------------------------------------------------------------------ write
    def write_episode(self, episode: EpisodeInput) -> WriteResult:
        result = WriteResult()
        date = episode.occurred_at

        for edge in episode.edges:
            try:
                decision = self._label(edge.actor, edge.cause)
                outcome = edge.effect
                context = self._compose_context(edge, episode)
                with self._lock:
                    out = self._mem.record_decision(
                        decision,
                        outcome,
                        _RELATION_MAP[edge.normalized_relation()],
                        edge.task_tag or "general",
                        confidence_source="llm_inferred",
                        context=context,
                    )
                result.ids.append(_extract_id(out))
                result.edges_written += 1
            except Exception as exc:  # never let one bad edge sink an episode
                log.warning("record_decision failed for %r: %s", edge.cause[:60], exc)
                result.errors.append(f"edge:{type(exc).__name__}: {exc}")

        for fact in episode.facts:
            try:
                with self._lock:
                    out = self._mem.record_fact(
                        fact.key,
                        fact.value,
                        scope=fact.scope,
                        confidence=fact.confidence,
                        replace_same_key=fact.replace_same_key,
                    )
                result.ids.append(_extract_id(out))
                result.facts_written += 1
            except Exception as exc:
                log.warning("record_fact failed for %r: %s", fact.key, exc)
                result.errors.append(f"fact:{type(exc).__name__}: {exc}")

        # `date` is threaded through via occurred_at on the edge context; the
        # binding exposes no per-edge date argument, so recency comes from write
        # order. Keep the value visible in logs for traceability.
        if date:
            log.debug("episode %r anchored at %s", episode.title, date)
        return result

    # ------------------------------------------------------------------- read
    def search(self, query: str, limit: int = 10, detail_level: str = "l1") -> str:
        with self._lock:
            return self._mem.search_memory(query, limit=limit, detail_level=detail_level) or ""

    def directory(self, limit: int = 20) -> str:
        with self._lock:
            return self._mem.causal_directory(limit=limit) or ""

    def facts(self, query: str, limit: int = 10) -> str:
        with self._lock:
            return self._mem.search_facts(query=query, limit=limit) or ""

    def reconstruct(self, query: str) -> str:
        with self._lock:
            return self._mem.reconstruct_lesson(query) or ""

    def stats(self) -> dict:
        info: dict = {"backend": self.name, "db": str(self.db_path)}
        try:
            info["db_bytes"] = self.db_path.stat().st_size
        except OSError:
            info["db_bytes"] = 0
        try:
            with self._lock:
                info["directory"] = (self._mem.causal_directory(limit=5) or "")[:400]
        except Exception as exc:
            info["error"] = str(exc)
        return info

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _label(actor: str, cause: str) -> str:
        prefix = _ACTOR_PREFIX.get(actor, "")
        return f"{prefix}{cause}" if prefix else cause

    @staticmethod
    def _compose_context(edge: CausalEdge, episode: EpisodeInput) -> str:
        """causal-memory forks comparable branches on (task_tag, context).

        Encoding speaker + episode title here makes those forks meaningful:
        "same project, same meeting" branches stay comparable.
        """
        bits: list[str] = []
        if episode.session_context:
            bits.append(episode.session_context)
        if episode.title:
            bits.append(episode.title)
        bits.append(f"说话人={edge.actor}")
        if edge.evidence:
            bits.append(f"原话：{edge.evidence[:120]}")
        return " | ".join(bits)[:400]


def _extract_id(rendered: str | None) -> str:
    """Pull ``id: 12`` out of causal-memory's human-readable confirmation string."""
    if not rendered:
        return ""
    marker = "id:"
    idx = rendered.rfind(marker)
    if idx == -1:
        return ""
    return rendered[idx + len(marker) :].strip().rstrip(")").strip()
