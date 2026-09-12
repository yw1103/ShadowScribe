"""Speech recognition via faster-whisper (CTranslate2).

Design notes:

* The model is loaded lazily and kept for the worker's lifetime — reloading a
  Whisper checkpoint per job would dominate runtime.
* ``HF_ENDPOINT`` is set *before* huggingface_hub is imported so a mainland host
  can pull checkpoints through a mirror. Setting it later has no effect.
* We never guess at domain vocabulary; ``SS_WHISPER_INITIAL_PROMPT`` primes the
  decoder with the user's own jargon instead of hard-coding anything.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AsrSegment:
    start_ms: int
    end_ms: int
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0


class AsrError(RuntimeError):
    pass


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
        model = self.load()
        started = time.time()

        segments_iter, info = model.transcribe(
            str(wav),
            language=language or self.s.whisper_language or None,
            beam_size=self.s.whisper_beam_size,
            vad_filter=self.s.whisper_vad_filter,
            vad_parameters={"min_silence_duration_ms": 500} if self.s.whisper_vad_filter else None,
            initial_prompt=self.s.whisper_initial_prompt or None,
            condition_on_previous_text=False,
            word_timestamps=False,
        )

        out: list[AsrSegment] = []
        for i, seg in enumerate(segments_iter):
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
            if i and i % 200 == 0:
                log.info("  ... %d segments decoded", i)

        detected = getattr(info, "language", None) or (language or self.s.whisper_language)
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
