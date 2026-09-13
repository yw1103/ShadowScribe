"""Windowed ASR tests.

The windowing exists because ``faster_whisper`` mel-spectrograms the *entire*
input in one STFT: peak RSS measured at 2.3 GB for 30 minutes and 7.2 GB for two
hours of audio, so a whole day in one file would need ~53 GB. These tests pin the
two properties that make windowing safe rather than merely smaller:

* a cut lands in a pause, never through a word, and
* timestamps come back absolute, in order, with no gap or overlap.

They use a stub model on purpose — the unit suite must pass with no Whisper
checkpoint, no numpy-free path assumptions and no network.
"""

from __future__ import annotations

import wave
from array import array
from pathlib import Path

import pytest

from shadowscribe.pipeline import asr
from shadowscribe.pipeline.asr import Transcriber, find_silence_cut


def _write_wav(path: Path, segments: list[tuple[int, int]], rate: int = 16_000) -> None:
    """Write a WAV from ``[(silence_ms, tone_ms), ...]`` blocks.

    A "tone" is a loud square wave, silence is zeroes — enough to make the RMS
    based cut search behave exactly as it does on real audio.
    """
    samples = array("h")
    amp = 12_000
    for silence_ms, tone_ms in segments:
        samples.extend(array("h", [0] * (rate * silence_ms // 1000)))
        period = rate // 100  # 100 Hz square wave
        block = array("h", [amp if (i % period) < period // 2 else -amp for i in range(period)])
        total = rate * tone_ms // 1000
        for _ in range(total // period):
            samples.extend(block)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(samples.tobytes())


class _Segment:
    """Mimics ``faster_whisper.Segment``: seconds, not milliseconds."""

    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = -0.1
        self.no_speech_prob = 0.01


class StubModel:
    """Records what it was asked to decode; returns one segment per window."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str | None]] = []

    def transcribe(self, source, **kwargs):
        length = len(source) if hasattr(source, "__len__") else 0
        self.calls.append((length, kwargs.get("language")))
        return (
            [_Segment(1.0, 2.0, f"window-{len(self.calls)}")],
            type("Info", (), {"language": "zh"})(),
        )


class StubTranscriber(Transcriber):
    """A Transcriber whose model is the stub above."""

    def __init__(self, settings, model: StubModel) -> None:
        super().__init__(settings)
        self._model = model

    def load(self):  # pragma: no cover - the stub is already loaded
        return self._model


@pytest.fixture()
def settings():
    from shadowscribe.config import Settings

    return Settings(
        data_dir=Path("/tmp"),
        asr_window_s=60,
        whisper_language="zh",
        whisper_vad_filter=False,
    )


class TestSilenceCut:
    def test_prefers_a_pause_over_the_exact_target(self):
        # 10 s of speech, 2 s of silence, 10 s of speech; target lands at 12 s.
        samples = array("h", [9000] * (16_000 * 10))
        samples.extend(array("h", [0] * (16_000 * 2)))
        samples.extend(array("h", [9000] * (16_000 * 10)))

        cut = find_silence_cut(samples, 16_000, 16_000 * 12)

        assert 16_000 * 10 <= cut <= 16_000 * 12, "must cut inside the silence"

    def test_falls_back_to_the_target_when_there_is_no_pause(self):
        samples = array("h", [9000] * (16_000 * 30))

        # Without this the window would shrink every time and the model would be
        # called an unbounded number of times.
        assert find_silence_cut(samples, 16_000, 16_000 * 20) == 16_000 * 20

    def test_ignores_silence_that_is_too_short_to_be_a_pause(self):
        samples = array("h", [9000] * (16_000 * 10))
        samples.extend(array("h", [0] * (16_000 // 10)))  # 100 ms
        samples.extend(array("h", [9000] * (16_000 * 10)))

        assert find_silence_cut(samples, 16_000, 16_000 * 11) == 16_000 * 11

    def test_does_not_look_further_back_than_the_search_window(self):
        # One long silence early in the file must not drag the cut far from the
        # target, or every window would collapse to the same tiny size.
        samples = array("h", [0] * (16_000 * 5))
        samples.extend(array("h", [9000] * (16_000 * 200)))

        cut = find_silence_cut(samples, 16_000, 16_000 * 200)

        assert cut >= 16_000 * (200 - asr.WINDOW_SEARCH_S)


class TestWindowedTranscribe:
    def test_short_files_still_take_the_single_pass_path(self, tmp_path, settings):
        wav = tmp_path / "short.wav"
        _write_wav(wav, [(100, 1000), (100, 1000)])  # ~2 s
        model = StubModel()
        tr = StubTranscriber(settings, model)

        segments, language = tr.transcribe(wav)

        assert len(model.calls) == 1, "under the window size nothing should split"
        assert language == "zh"
        assert [s.text for s in segments] == ["window-1"]

    def test_long_files_are_split_and_offsets_are_absolute(self, tmp_path, settings):
        # 150 s against a 60 s window → at least three passes.
        wav = tmp_path / "long.wav"
        _write_wav(wav, [(500, 5000)] * 27 + [(500, 1000)])
        model = StubModel()
        tr = StubTranscriber(settings, model)

        segments, _ = tr.transcribe(wav)

        assert len(model.calls) >= 3
        # Stub always reports 1–2 s *within* its window; absolute timestamps must
        # therefore march forward by roughly one window each time.
        starts = [s.start_ms for s in segments]
        assert starts == sorted(starts)
        assert starts[-1] >= 60_000, "later windows must be offset, not restarted"
        assert all(s.end_ms > s.start_ms for s in segments)

    def test_windows_cover_the_audio_without_gaps(self, tmp_path, settings):
        wav = tmp_path / "cover.wav"
        _write_wav(wav, [(500, 5000)] * 27 + [(500, 1000)])
        model = StubModel()
        tr = StubTranscriber(settings, model)

        tr.transcribe(wav)

        decoded = sum(length for length, _ in model.calls) / 16_000
        with wave.open(str(wav), "rb") as fh:
            total = fh.getnframes() / fh.getframerate()
        assert decoded == pytest.approx(total, abs=0.5), "no audio may be skipped"

    def test_language_is_detected_once_and_reused(self, tmp_path, settings):
        wav = tmp_path / "lang.wav"
        _write_wav(wav, [(500, 5000)] * 27 + [(500, 1000)])
        model = StubModel()
        tr = StubTranscriber(settings, model)

        tr.transcribe(wav)

        # Re-detecting per window would let a quiet stretch flip the language
        # halfway through a day.
        assert [lang for _, lang in model.calls] == ["zh"] * len(model.calls)

    def test_windowing_can_be_switched_off(self, tmp_path, settings):
        settings.asr_window_s = 0
        wav = tmp_path / "off.wav"
        _write_wav(wav, [(500, 5000)] * 27)
        model = StubModel()
        tr = StubTranscriber(settings, model)

        tr.transcribe(wav)

        assert len(model.calls) == 1

    def test_stereo_input_falls_back_instead_of_misreading_samples(self, tmp_path, settings):
        # The pipeline only ever writes mono, but a hand-made file reaching this
        # path must not be reinterpreted as interleaved garbage.
        wav = tmp_path / "stereo.wav"
        mono = tmp_path / "mono.wav"
        _write_wav(mono, [(500, 5000)] * 27)
        with wave.open(str(mono), "rb") as src:
            frames = src.readframes(src.getnframes())
            rate = src.getframerate()
        doubled = b"".join(frames[i : i + 2] * 2 for i in range(0, len(frames), 2))
        with wave.open(str(wav), "wb") as fh:
            fh.setnchannels(2)
            fh.setsampwidth(2)
            fh.setframerate(rate)
            fh.writeframes(doubled)

        model = StubModel()
        tr = StubTranscriber(settings, model)

        tr.transcribe(wav)

        assert len(model.calls) == 1, "stereo must take the direct path, not windowed"
