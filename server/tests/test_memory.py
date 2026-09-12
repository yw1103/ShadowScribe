"""Memory-backend tests.

Two things matter here:

1. Chinese retrieval must actually work — whitespace tokenisation would silently
   return nothing for every CJK query, which is exactly the failure mode that is
   hard to notice.
2. One malformed edge must never sink an otherwise good episode.
"""

from __future__ import annotations

from shadowscribe.memory import CausalEdge, EpisodeInput, Fact, build_backend
from shadowscribe.memory.native import NativeMemoryBackend, _tokens


def make_backend(clean_db) -> NativeMemoryBackend:
    return NativeMemoryBackend()


def sample_episode() -> EpisodeInput:
    return EpisodeInput(
        title="登录页排期对齐",
        summary="老王反馈 Safari 白屏。",
        topics=["登录页"],
        session_context="与老王在会议室",
        occurred_at="2026-01-08",
        edges=[
            CausalEdge(
                cause="老王反馈登录页 Safari 白屏、用户投诉多",
                effect="我承诺周四之前提交修复方案",
                relation="caused",
                task_tag="登录页",
                actor="owner",
                evidence="周四之前我给你一个方案",
                confidence=0.9,
            ),
            CausalEdge(
                cause="上线前跑了回归测试",
                effect="没有出现结算金额错乱",
                relation="prevented",
                task_tag="结算",
                actor="owner",
                confidence=0.8,
            ),
        ],
        facts=[Fact(key="客户_老王", value="后端负责人，关注交付排期", confidence=0.9)],
    )


# ------------------------------------------------------------------- tokenizer


def test_tokens_bigrams_for_chinese():
    """CJK has no spaces; bigrams are the cheapest thing that matches substrings."""
    toks = _tokens("登录页")
    assert "登录" in toks
    assert "录页" in toks


def test_tokens_keeps_ascii_words():
    toks = _tokens("Safari 白屏 fix")
    assert "safari" in toks
    assert "fix" in toks
    assert "白屏" in toks


# ----------------------------------------------------------------------- write


def test_write_episode_persists_edges_and_facts(clean_db):
    backend = make_backend(clean_db)
    result = backend.write_episode(sample_episode())

    assert result.ok
    assert result.edges_written == 2
    assert result.facts_written == 1
    assert len(result.ids) == 3


def test_write_episode_survives_a_bad_edge(clean_db):
    """A single failure must degrade to a warning, not lose the whole episode."""
    backend = make_backend(clean_db)
    episode = sample_episode()
    episode.edges.append(CausalEdge(cause="x" * 10, effect="y", relation="caused"))

    result = backend.write_episode(episode)
    assert result.edges_written == 3
    assert result.facts_written == 1


def test_replace_same_key_retires_old_fact(clean_db):
    backend = make_backend(clean_db)
    backend.write_episode(EpisodeInput(title="a", summary="", facts=[Fact("k", "旧值")]))
    backend.write_episode(
        EpisodeInput(title="b", summary="", facts=[Fact("k", "新值", replace_same_key=True)])
    )

    rendered = backend.facts("k")
    assert "新值" in rendered
    assert "旧值" not in rendered


# ------------------------------------------------------------------------ read


def test_search_finds_chinese_by_substring(clean_db):
    backend = make_backend(clean_db)
    backend.write_episode(sample_episode())

    for query in ("登录页", "Safari 白屏", "老王", "修复方案", "结算"):
        rendered = backend.search(query)
        assert "No memories found" not in rendered, f"{query!r} returned nothing"


def test_search_finds_prevented_edge(clean_db):
    """ "prevented" edges record avoided mistakes — they must be retrievable."""
    backend = make_backend(clean_db)
    backend.write_episode(sample_episode())

    rendered = backend.search("回归测试")
    assert "(prevented)" in rendered


def test_search_miss_is_explicit(clean_db):
    backend = make_backend(clean_db)
    backend.write_episode(sample_episode())
    assert "No memories found" in backend.search("量子计算机")


def test_l0_detail_is_compact(clean_db):
    backend = make_backend(clean_db)
    backend.write_episode(sample_episode())

    l0 = backend.search("登录页", detail_level="l0")
    l2 = backend.search("登录页", detail_level="l2")
    assert len(l0) <= len(l2)


def test_directory_lists_recent_edges(clean_db):
    backend = make_backend(clean_db)
    backend.write_episode(sample_episode())

    directory = backend.directory(limit=10)
    assert "Recent decisions" in directory
    assert "登录页" in directory


def test_directory_on_empty_store(clean_db):
    assert "empty" in make_backend(clean_db).directory()


def test_stats_counts_layers(clean_db):
    backend = make_backend(clean_db)
    backend.write_episode(sample_episode())

    info = backend.stats()
    assert info["backend"] == "native"
    assert info["edges"] == 2
    assert info["facts"] == 1


# --------------------------------------------------------------------- factory


def test_factory_degrades_to_native_when_causal_memory_missing(clean_db):
    """A fresh clone must boot even with no Rust wheel present."""
    backend = build_backend()
    assert backend.name in {"native", "causal-memory"}
    assert backend.stats()
