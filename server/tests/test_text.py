"""Chinese normalisation tests.

The failure this guards against is invisible: mixed Simplified/Traditional output
does not raise, it just makes half the history unfindable.
"""

from __future__ import annotations

import pytest

from shadowscribe.pipeline import text as text_mod
from shadowscribe.pipeline.asr import AsrSegment


def seg(value: str) -> AsrSegment:
    return AsrSegment(start_ms=0, end_ms=1000, text=value)


@pytest.mark.skipif(not text_mod.available(), reason="OpenCC not installed")
def test_traditional_is_converted():
    assert text_mod.to_simplified("登錄頁") == "登录页"
    assert text_mod.to_simplified("用戶反饋") == "用户反馈"
    assert text_mod.to_simplified("結算模塊") == "结算模块"


@pytest.mark.skipif(not text_mod.available(), reason="OpenCC not installed")
def test_simplified_input_is_unchanged():
    """t2s on already-Simplified text must be a no-op, not a mangling."""
    original = "登录页在 Safari 上打开是白屏，iOS 用户投诉涨得挺快。"
    assert text_mod.to_simplified(original) == original


@pytest.mark.skipif(not text_mod.available(), reason="OpenCC not installed")
def test_mixed_script_line_is_normalised():
    mixed = "我看了下用戶反馈，登录页在 Safari 上白屏"
    assert text_mod.to_simplified(mixed) == "我看了下用户反馈，登录页在 Safari 上白屏"


@pytest.mark.skipif(not text_mod.available(), reason="OpenCC not installed")
def test_normalize_segments_rewrites_in_place():
    segments = [seg("登錄頁白屏"), seg("結算模塊對賬錯亂")]
    returned = text_mod.normalize_segments(segments)
    assert returned is segments
    assert [s.text for s in segments] == ["登录页白屏", "结算模块对账错乱"]


def test_normalize_segments_disabled_is_a_noop():
    segments = [seg("登錄頁白屏")]
    text_mod.normalize_segments(segments, enabled=False)
    assert segments[0].text == "登錄頁白屏"


def test_empty_text_is_safe():
    assert text_mod.to_simplified("") == ""


def test_degrades_without_opencc(monkeypatch):
    """A lean install without opencc must still run the pipeline."""
    monkeypatch.setattr(text_mod, "_converter", None)
    monkeypatch.setattr(text_mod, "_unavailable", True)
    assert text_mod.available() is False
    assert text_mod.to_simplified("登錄頁") == "登錄頁"
    assert text_mod.normalize_segments([seg("登錄頁")])[0].text == "登錄頁"
