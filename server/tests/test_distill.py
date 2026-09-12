"""Distillation tests — the highest-risk component.

A hallucinated commitment poisons every future context injection, so the parser
is tested for what it *rejects* at least as much as for what it accepts.
"""

from __future__ import annotations

import pytest

from shadowscribe.pipeline.asr import AsrSegment
from shadowscribe.pipeline.diarize import GUEST, OWNER
from shadowscribe.pipeline.distill import (
    Distiller,
    _iso_date,
    _strip_code_fence,
    chunk_segments,
    render_transcript,
)


def seg(start_s: float, end_s: float, text: str) -> AsrSegment:
    return AsrSegment(start_ms=int(start_s * 1000), end_ms=int(end_s * 1000), text=text)


@pytest.fixture()
def distiller():
    from shadowscribe.config import settings

    return Distiller(settings)


# ------------------------------------------------------------------ helpers


def test_strip_code_fence_handles_json_blocks():
    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('```\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('{"a":1}') == '{"a":1}'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-08", "2026-01-08"),
        ("2026-01-08T00:00:00", "2026-01-08"),
        ("null", None),
        ("", None),
        ("周四", None),
        (None, None),
    ],
)
def test_iso_date_is_strict(raw, expected):
    """Relative dates must NOT be guessed at this layer — the LLM resolves them."""
    assert _iso_date(raw) == expected


def test_render_transcript_labels_speakers():
    text = render_transcript([seg(0, 2, "你好"), seg(2, 4, "在")], [OWNER, GUEST])
    assert "主人(00:00): 你好" in text
    assert "对方(00:02): 在" in text


def test_render_transcript_without_labels_marks_unknown():
    assert "未知" in render_transcript([seg(0, 1, "喂")], None)


# ------------------------------------------------------------------ chunking


def test_chunk_segments_splits_by_duration():
    segments = [seg(i * 60, (i + 1) * 60, f"第{i}分钟") for i in range(30)]
    windows = chunk_segments(segments, window_s=600)
    assert len(windows) >= 3
    assert sum(len(w) for w in windows) == len(segments)


def test_chunk_segments_honours_min_chars():
    """A window of pure silence must not cost an LLM call."""
    quiet = [seg(i, i + 1, "嗯") for i in range(20)]
    assert chunk_segments(quiet, window_s=5, min_chars=40) == []
    assert chunk_segments(quiet, window_s=5, min_chars=0)


def test_chunk_segments_empty_input():
    assert chunk_segments([], window_s=600) == []


# -------------------------------------------------------------------- parsing


REALISTIC_PAYLOAD = {
    "title": "登录页排期对齐",
    "summary": "老王反馈 Safari 白屏影响用户投诉，双方同意优先修复兼容性。",
    "topics": ["登录页", "排期"],
    "entities": [
        {"name": "老王", "kind": "person", "aliases": ["王工"]},
        {"name": "登录页", "kind": "project"},
        {"name": "", "kind": "person"},  # must be dropped
    ],
    "facts": [
        {"key": "客户_老王", "value": "后端负责人，关注交付排期", "confidence": 0.9},
        {"key": "", "value": "无 key 应被丢弃", "confidence": 0.9},
        {"key": "项目_登录页", "value": "", "confidence": 0.9},
    ],
    "edges": [
        {
            "cause": "老王反馈 Safari 白屏、用户投诉多",
            "effect": "我承诺周四之前提交修复方案",
            "relation": "caused",
            "actor": "owner",
            "task_tag": "登录页",
            "evidence": "周四之前我给你一个方案",
            "confidence": 0.9,
        },
        {
            # relation is nonsense → must fall back to "caused", not crash
            "cause": "上线前跑了回归测试",
            "effect": "没有出现结算事故",
            "relation": "obviously-not-a-relation",
            "actor": "guest",  # legacy label → normalised to "other"
            "task_tag": "",
            "confidence": 0.8,
        },
        {"cause": "", "effect": "孤儿结果", "confidence": 0.9},  # dropped
        "not-a-dict",  # dropped
    ],
    "commitments": [
        {
            "what": "提交登录页 Safari 白屏修复方案",
            "owner": "我",
            "counterparty": "老王",
            "due_text": "周四",
            "due_date": "2026-01-08",
            "confidence": 0.9,
        },
        {"what": "", "owner": "我"},  # dropped
    ],
    "decisions": [
        {
            "what": "登录页先兼容 Safari，新功能排期后延",
            "rationale": "用户投诉集中",
            "actor": "owner",
        }
    ],
}


def test_parse_keeps_valid_and_drops_malformed(distiller):
    result = distiller._parse(REALISTIC_PAYLOAD, recorded_at="2026-01-08")

    assert result.title == "登录页排期对齐"
    assert result.topics == ["登录页", "排期"]
    assert not result.degraded

    # blank entities/facts/edges are dropped
    assert [e["name"] for e in result.entities] == ["老王", "登录页"]
    assert [f.key for f in result.facts] == ["客户_老王"]
    assert len(result.edges) == 2
    assert len(result.commitments) == 1
    assert len(result.decisions) == 1


def test_parse_normalises_relation_and_actor(distiller):
    result = distiller._parse(REALISTIC_PAYLOAD, recorded_at="2026-01-08")
    good, odd = result.edges

    assert good.normalized_relation() == "caused"
    assert good.actor == OWNER
    # unknown relation must not survive into the memory store
    assert odd.normalized_relation() == "caused"
    assert odd.actor == "other"
    # an empty task_tag falls back to the first topic
    assert odd.task_tag == "登录页"


def test_parse_clamps_confidence(distiller):
    result = distiller._parse(
        {"edges": [{"cause": "a", "effect": "b", "confidence": 9.9}]},
        recorded_at="2026-01-08",
    )
    assert result.edges[0].confidence == 1.0

    result = distiller._parse(
        {"edges": [{"cause": "a", "effect": "b", "confidence": "not-a-number"}]},
        recorded_at="2026-01-08",
    )
    assert 0.0 <= result.edges[0].confidence <= 1.0


def test_parse_tolerates_garbage_types(distiller):
    """The model is instructed to emit JSON, but it will not always obey."""
    result = distiller._parse(
        {
            "title": 12345,
            "summary": None,
            "topics": "登录页",  # not a list
            "entities": None,
            "edges": {"cause": "x"},  # not a list
            "commitments": 7,
            "decisions": [],
        },
        recorded_at="2026-01-08",
    )
    assert result.title == "12345"
    assert result.summary == ""
    assert result.topics == []
    assert result.edges == []
    assert result.commitments == []
    assert result.decisions == []


def test_parse_marks_edges_with_recording_date(distiller):
    result = distiller._parse(REALISTIC_PAYLOAD, recorded_at="2026-01-08")
    assert all(e.occurred_at == "2026-01-08" for e in result.edges)


# ------------------------------------------------------------------ degradation


def test_degraded_mode_without_llm_key(distiller):
    """No API key must still produce a searchable episode, never an exception."""
    assert distiller.enabled is False
    result = distiller.distill(
        [seg(0, 3, "我们在会议室讨论了登录页的问题")],
        labels=None,
        recorded_at="2026-01-08",
    )
    assert result.degraded is True
    assert result.edges == []
    assert result.commitments == []
    assert "登录页" in result.summary


def test_distill_long_returns_one_result_per_window(distiller):
    segments = [seg(i * 60, (i + 1) * 60, f"第{i}分钟我们讨论了排期和交付") for i in range(30)]
    results = distiller.distill_long(segments, recorded_at="2026-01-08")
    assert len(results) >= 3
    assert all(r.degraded for r in results)


def test_to_episode_input_carries_context(distiller):
    result = distiller._parse(REALISTIC_PAYLOAD, recorded_at="2026-01-08")
    episode = result.to_episode_input(occurred_at="2026-01-08", session_context="与老王在会议室")
    assert episode.occurred_at == "2026-01-08"
    assert episode.session_context == "与老王在会议室"
    assert len(episode.edges) == 2
