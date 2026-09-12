"""Dependency-free SQLite memory backend.

Exists so the pipeline runs with nothing but ``pip install -r requirements.txt``:
no Rust wheel, no LLM key. Retrieval is substring/BM25-lite over a flat edge table
— good enough to demo the loop, deliberately weaker than `causal-memory`, which
owns spreading activation, consolidation and counterfactual replay.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlmodel import Field, Session, SQLModel, select

from ..db import get_engine
from .base import CausalEdge, EpisodeInput, WriteResult


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemEdge(SQLModel, table=True):
    __tablename__ = "mem_edges"

    id: int | None = Field(default=None, primary_key=True)
    cause: str = ""
    effect: str = ""
    relation: str = "caused"
    task_tag: str = "general"
    context: str | None = None
    actor: str = "unknown"
    evidence: str | None = None
    confidence: float = 0.0
    occurred_at: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class MemFact(SQLModel, table=True):
    __tablename__ = "mem_facts"

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True)
    value: str = ""
    scope: str = "user"
    confidence: float = 0.7
    created_at: datetime = Field(default_factory=_utcnow)


_CJK = re.compile(r"[\u4e00-\u9fff]+")
_ASCII = re.compile(r"[A-Za-z0-9_]+")
_RELATION_GLYPH = {
    "caused": "→(caused)→",
    "enabled": "→(enabled)→",
    "prevented": "→(prevented)→",
    "no_effect": "→(no_effect)→",
}


def _tokens(text: str) -> list[str]:
    """CJK bigrams + ASCII words — the cheapest thing that actually matches Chinese."""
    out: list[str] = [w.lower() for w in _ASCII.findall(text)]
    for run in _CJK.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


class NativeMemoryBackend:
    name = "native"

    def __init__(self, db_path=None) -> None:
        self.engine = get_engine(db_path)

    # ------------------------------------------------------------------ write
    def write_episode(self, episode: EpisodeInput) -> WriteResult:
        result = WriteResult()
        with Session(self.engine) as s:
            for edge in episode.edges:
                try:
                    row = MemEdge(
                        cause=edge.cause,
                        effect=edge.effect,
                        relation=edge.normalized_relation(),
                        task_tag=edge.task_tag or "general",
                        context=edge.context or episode.session_context,
                        actor=edge.actor,
                        evidence=edge.evidence,
                        confidence=edge.confidence,
                        occurred_at=edge.occurred_at or episode.occurred_at,
                    )
                    s.add(row)
                    s.flush()
                    result.ids.append(str(row.id))
                    result.edges_written += 1
                except Exception as exc:
                    result.errors.append(f"edge:{type(exc).__name__}: {exc}")
            for fact in episode.facts:
                try:
                    if fact.replace_same_key:
                        for old in s.exec(
                            select(MemFact).where(
                                MemFact.key == fact.key, MemFact.scope == fact.scope
                            )
                        ).all():
                            s.delete(old)
                    row = MemFact(
                        key=fact.key,
                        value=fact.value,
                        scope=fact.scope,
                        confidence=fact.confidence,
                    )
                    s.add(row)
                    s.flush()
                    result.ids.append(str(row.id))
                    result.facts_written += 1
                except Exception as exc:
                    result.errors.append(f"fact:{type(exc).__name__}: {exc}")
            s.commit()
        return result

    # ------------------------------------------------------------------- read
    def search(self, query: str, limit: int = 10, detail_level: str = "l1") -> str:
        toks = _tokens(query)
        with Session(self.engine) as s:
            edges = s.exec(select(MemEdge).order_by(MemEdge.id.desc()).limit(2000)).all()
            facts = s.exec(select(MemFact).order_by(MemFact.id.desc()).limit(2000)).all()

        scored_edges = sorted(
            ((self._score(toks, f"{e.cause} {e.effect} {e.task_tag} {e.context or ''}"), e) for e in edges),
            key=lambda pair: pair[0],
            reverse=True,
        )
        scored_facts = sorted(
            ((self._score(toks, f"{f.key} {f.value}"), f) for f in facts),
            key=lambda pair: pair[0],
            reverse=True,
        )
        hits_e = [(sc, e) for sc, e in scored_edges if sc > 0][:limit]
        hits_f = [(sc, f) for sc, f in scored_facts if sc > 0][:limit]

        if not hits_e and not hits_f:
            return f"[native] No memories found matching {query!r}."

        lines = [f"[native] Found {len(hits_e) + len(hits_f)} memories:"]
        if hits_f and detail_level != "l0":
            lines.append("\n Facts:")
            for _, f in hits_f:
                lines.append(f"  #{f.id} [{f.scope}] {f.key} = {f.value!r}")
        elif hits_f:
            lines.append("\n Facts: " + ", ".join(f.key for _, f in hits_f))
        if hits_e:
            lines.append("\n Causal lessons:")
            for _, e in hits_e:
                glyph = _RELATION_GLYPH.get(e.relation, "→")
                lines.append(f"  #{e.id} [{e.task_tag}] {e.cause} {glyph} {e.effect}")
        return "\n".join(lines)

    def directory(self, limit: int = 20) -> str:
        with Session(self.engine) as s:
            edges = s.exec(select(MemEdge).order_by(MemEdge.id.desc()).limit(limit)).all()
        if not edges:
            return "# Recent decisions (causal memory)\n\n_(empty)_"
        lines = ["# Recent decisions (causal memory)", ""]
        for e in edges:
            glyph = _RELATION_GLYPH.get(e.relation, "→")
            lines.append(f"- [{e.task_tag}] {e.cause} {glyph} {e.effect}")
        return "\n".join(lines)

    def facts(self, query: str, limit: int = 10) -> str:
        toks = _tokens(query)
        with Session(self.engine) as s:
            rows = s.exec(select(MemFact).order_by(MemFact.id.desc()).limit(2000)).all()
        hits = sorted(
            ((self._score(toks, f"{r.key} {r.value}"), r) for r in rows),
            key=lambda p: p[0],
            reverse=True,
        )
        hits = [(sc, r) for sc, r in hits if sc > 0][:limit]
        if not hits:
            return f"[native] No facts matching {query!r}."
        return "[native] Found facts:\n" + "\n".join(
            f"  {r.id}. [{r.scope}] {r.key} = {r.value!r}" for _, r in hits
        )

    def reconstruct(self, query: str) -> str:
        return self.search(query, limit=5, detail_level="l2")

    def stats(self) -> dict:
        with Session(self.engine) as s:
            n_edges = len(s.exec(select(MemEdge.id)).all())
            n_facts = len(s.exec(select(MemFact.id)).all())
        return {"backend": self.name, "edges": n_edges, "facts": n_facts}

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _score(query_tokens: list[str], text: str) -> float:
        if not query_tokens:
            return 0.0
        hay = text.lower()
        hay_tokens = set(_tokens(text))
        score = 0.0
        for tok in query_tokens:
            if tok in hay_tokens:
                score += 2.0
            elif tok in hay:
                score += 1.0
        return score
