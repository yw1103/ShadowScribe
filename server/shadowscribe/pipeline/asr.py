"""Speech recognition via faster-whisper (CTranslate2).

Design notes:

* The model is loaded lazily and kept for the worker's lifetime — reloading a
  Whisper checkpoint per job would dominate runtime.
* ``HF_ENDPOINT`` is set *before* huggingface_hub is imported so a mainland host
  can pull checkpoints through a mirror. Setting it later has no effect.
* We never guess at domain vocabulary; ``SS_WHISPER_INITIAL_PROMPT`` primes the
  decoder with the user's own jargon instead of hard-coding anything.
* Long recordings are decoded in windows (see ``_transcribe_windows``). This is
  not an optimisation: ``faster_whisper`` builds the log-Mel spectrogram of the
  entire input in one STFT, so peak RSS grows ~3.3 GB per hour of audio. Measured
  on the reference box: 0.5 h → 2.3 GB, 2 h → 7.2 GB. A whole day in one file
  would want ~53 GB and take the host down with it.
"""

from __future__ import annotations

import logging
import os
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: How far back from a window edge to hunt for a pause to cut in. Long enough to
#: find a breath, short enough that windows do not drift far below their target.
WINDOW_SEARCH_S = 60

_SILENCE_FRAME_MS = 50
_SILENCE_RUN_MS = 300
#: ≈ -40 dBFS. Above this a frame counts as speech, and cutting there would chop
#: a word in half — the one thing windowing must never do.
_SILENCE_RMS = 0.01


@dataclass(slots=True)
class AsrSegment:
    start_ms: int
    end_ms: int
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0


class AsrError(RuntimeError):
    pass


def _rms(samples: array, start: int, length: int) -> float:
    """Root-mean-square of one frame, normalised to [-1, 1]."""
    if length <= 0:
        return 0.0
    acc = 0
    for value in samples[start : start + length]:
        acc += value * value
    return (acc / length) ** 0.5 / 32768.0


def find_silence_cut(samples: array, rate: int, target: int) -> int:
    """Pick where to end a window near ``target``, preferring a pause.

    Cutting mid-word is the one thing windowing must not do, so this looks back
    over the tail for the longest run of quiet frames and returns a point inside
    it. If the tail holds no real pause we return ``target`` unchanged: silently
    shrinking the window every time would trade a memory bound for an unbounded
    number of model calls.
    """
    frame = max(1, rate * _SILENCE_FRAME_MS // 1000)
    run_needed = max(1, _SILENCE_RUN_MS // _SILENCE_FRAME_MS)
    lo = max(0, target - rate * WINDOW_SEARCH_S)
    best: tuple[int, int] | None = None  # (run length, cut index)
    run = 0
    for idx in range(lo, min(target, len(samples)), frame):
        if _rms(samples, idx, frame) < _SILENCE_RMS:
            run += 1
            if run >= run_needed and (best is None or run >= best[0]):
                # Cut in the middle of the quiet frame rather than at its edge,
                # so neither window starts or ends on a half-decoded breath.
                best = (run, idx + frame // 2)
        else:
            run = 0
    return best[1] if best else target


def _wav_shape(path: Path) -> tuple[int, int, int]:
    """Return ``(rate, channels, frames)`` for a PCM WAV."""
    with wave.open(str(path), "rb") as fh:
        return fh.getframerate(), fh.getnchannels(), fh.getnframes()


def _read_pcm(path: Path, start_frame: int, frames: int) -> array:
    """Read a slice of a 16-bit PCM WAV as raw samples, without decoding it all."""
    with wave.open(str(path), "rb") as fh:
        if fh.getsampwidth() != 2:
            raise AsrError("windowed ASR needs 16-bit PCM; normalise the audio first")
        fh.setpos(max(0, min(start_frame, fh.getnframes())))
        return _from_bytes(fh.readframes(max(0, frames)))


def _from_bytes(raw: bytes) -> array:
    out = array("h")
    out.frombytes(raw)
    return out


def _to_float32(samples: array):
    """Convert int16 PCM to the float32 numpy array Whisper expects.

    ``numpy`` arrives with ``faster_whisper``; importing it here keeps it out of
    the import path of everything that only ever touches short files.
    """
    import numpy as np

    return np.frombuffer(samples.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0


class Transcriber:
    """Lazily-loaded, reusable faster-whisper wrapper."""

    def __init__(self, settings) -> None:
        self.s = settings
        self._model = None
        self._loaded_at: float | None = None

    # ------------------------------------------------------------------ model
    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self):
        if self._model is not None:
            return self._model

        # Both must be set before huggingface_hub resolves its config. Setting
        # them later has no effect, which is why nothing else in the codebase is
        # allowed to import huggingface_hub directly.
        if self.s.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", self.s.hf_endpoint)
            os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", self.s.hf_endpoint)
        if self.s.hf_disable_xet:
            # Mirrors do not proxy cas-server.xethub.hf.co; without this the
            # download dies with a 401 that looks nothing like a mirror problem.
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

        from faster_whisper import WhisperModel

        self.s.models_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()
        log.info(
            "loading whisper model=%s device=%s compute=%s",
            self.s.whisper_model,
            self.s.whisper_device,
            self.s.whisper_compute_type,
        )
        kwargs = {
            "device": self.s.whisper_device,
            "compute_type": self.s.whisper_compute_type,
            "download_root": str(self.s.models_dir),
        }
        if self.s.asr_cpu_threads:
            kwargs["cpu_threads"] = self.s.asr_cpu_threads

        try:
            self._model = WhisperModel(self.s.whisper_model, **kwargs)
        except Exception as exc:
            raise AsrError(
                f"could not load whisper model {self.s.whisper_model!r}: {exc}. "
                f"Check SS_HF_ENDPOINT / network access, or pre-place the model under "
                f"{self.s.models_dir}."
            ) from exc

        self._loaded_at = time.time()
        log.info("whisper model ready in %.1fs", self._loaded_at - started)
        return self._model

    # -------------------------------------------------------------- inference
    def transcribe(self, wav: Path, *, language: str | None = None) -> tuple[list[AsrSegment], str]:
        """Return ``(segments, detected_language)`` for a 16 kHz mono WAV."""
        started = time.time()
        window_s = int(getattr(self.s, "asr_window_s", 0) or 0)

        out: list[AsrSegment] | None = None
        detected = ""
        if window_s > 0:
            try:
                rate, channels, frames = _wav_shape(wav)
            except (OSError, wave.Error):
                rate = 0
            # Anything short enough to sit in RAM comfortably takes the direct
            # path, so windowing cannot change the result for normal captures.
            if rate and channels == 1 and frames > window_s * rate:
                out, detected = self._transcribe_windows(wav, rate, frames, window_s, language)

        if out is None:
            out, detected = self._decode(str(wav), language)

        elapsed = time.time() - started
        audio_s = (out[-1].end_ms / 1000) if out else 0.0
        log.info(
            "ASR done: %d segments, %.1fs audio in %.1fs (%.1fx realtime), lang=%s",
            len(out),
            audio_s,
            elapsed,
            (audio_s / elapsed) if elapsed > 0 else 0.0,
            detected,
        )
        return out, detected

    def _decode(self, source, language: str | None) -> tuple[list[AsrSegment], str]:
        """Run the model over one path or in-memory window."""
        model = self.load()
        segments_iter, info = model.transcribe(
            source,
            language=language or self.s.whisper_language or None,
            beam_size=self.s.whisper_beam_size,
            vad_filter=self.s.whisper_vad_filter,
            vad_parameters={"min_silence_duration_ms": 500} if self.s.whisper_vad_filter else None,
            initial_prompt=self.s.whisper_initial_prompt or None,
            condition_on_previous_text=False,
            word_timestamps=False,
        )

        out: list[AsrSegment] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text:
                continue
            out.append(
                AsrSegment(
                    start_ms=int(seg.start * 1000),
                    end_ms=int(seg.end * 1000),
                    text=text,
                    avg_logprob=float(getattr(seg, "avg_logprob", 0.0) or 0.0),
                    no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
                )
            )
        detected = getattr(info, "language", None) or (language or self.s.whisper_language)
        return out, detected

    def _transcribe_windows(
        self, wav: Path, rate: int, total_frames: int, window_s: int, language: str | None
    ) -> tuple[list[AsrSegment], str]:
        """Decode a long WAV in bounded windows and stitch absolute timestamps.

        Only ``window_s + WINDOW_SEARCH_S`` seconds of PCM are ever resident, so
        peak memory stops depending on how long the recording is. The first
        window also fixes the language for the rest: re-detecting it per window
        would let a quiet stretch flip a Chinese day to English halfway through.
        """
        window = window_s * rate
        lookahead = WINDOW_SEARCH_S * rate
        out: list[AsrSegment] = []
        detected: str | None = None
        start = 0
        index = 0
        total_windows = max(1, -(-total_frames // window))
        while start < total_frames:
            samples = _read_pcm(wav, start, min(window + lookahead, total_frames - start))
            if not len(samples):
                break
            cut = (
                len(samples)
                if start + window >= total_frames
                else find_silence_cut(samples, rate, min(window, len(samples)))
            )
            # A pathological cut (all-digital-silence audio can land at 0) must
            # not spin this loop forever.
            cut = min(max(cut, rate), len(samples))
            index += 1
            log.info(
                "ASR window %d/%d: %.0fs → %.0fs",
                index,
                total_windows,
                start / rate,
                (start + cut) / rate,
            )
            segments, lang = self._decode(_to_float32(samples[:cut]), language or detected)
            detected = detected or lang
            offset_ms = int(start * 1000 / rate)
            for seg in segments:
                seg.start_ms += offset_ms
                seg.end_ms += offset_ms
            out.extend(segments)
            start += cut
        return out, detected or (language or self.s.whisper_language)
