"""Chinese text normalisation.

Whisper's smaller checkpoints emit a mix of Simplified and Traditional
characters, sometimes within one sentence ("我看了下用戶反馈，登录页在Safari上白屏").
That is not a cosmetic problem here: retrieval is substring based, so
``登录页`` and ``登錄頁`` become two unrelated memories for the same thing, and
``ss search`` silently misses half the history.

Converting once, right after ASR, keeps every downstream artefact — segments,
memory facts, causal edges, the context card — on one orthography.

The dependency is optional. Without it this module is a no-op rather than an
error, so a lean install still runs end to end.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_converter = None
_unavailable = False
_lock = threading.Lock()


def _get_converter():
    global _converter, _unavailable
    if _converter is not None or _unavailable:
        return _converter
    with _lock:
        if _converter is not None or _unavailable:
            return _converter
        try:
            from opencc import OpenCC

            # t2s = Traditional -> Simplified. Applied to already-Simplified text
            # it is a no-op, so we never need to detect which script we got.
            _converter = OpenCC("t2s")
            log.info("OpenCC t2s loaded; Chinese transcripts will be normalised")
        except Exception as exc:
            _unavailable = True
            log.info(
                "OpenCC unavailable (%s); Chinese text will keep whatever script "
                "Whisper produced. Install `opencc-python-reimplemented` to fix "
                "Simplified/Traditional search mismatches.",
                exc,
            )
    return _converter


def available() -> bool:
    return _get_converter() is not None


def to_simplified(text: str) -> str:
    """Best-effort Traditional → Simplified. Returns the input unchanged if unavailable."""
    if not text:
        return text
    converter = _get_converter()
    if converter is None:
        return text
    try:
        return converter.convert(text)
    except Exception as exc:  # a converter bug must never fail a job
        log.debug("OpenCC conversion failed (%s); keeping original text", exc)
        return text


def normalize_segments(segments, *, enabled: bool = True):
    """Rewrite ``seg.text`` in place. Returns the same list for chaining."""
    if not enabled:
        return segments
    converter = _get_converter()
    if converter is None:
        return segments
    for seg in segments:
        seg.text = to_simplified(seg.text)
    return segments
