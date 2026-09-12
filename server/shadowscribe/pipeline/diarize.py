"""Speaker attribution — "who said this line?"

MVP scope is deliberately narrow and matches 原则二 (voiceprint sovereignty):

* The owner's voiceprint is *enrolled* once from a short sample.
* Each ASR segment is embedded and compared by cosine similarity.
* Above ``SS_OWNER_THRESHOLD`` → ``owner``, otherwise → ``guest``.

Full multi-party diarization (clustering several unknown guests) is a phase-2
item; for causal correctness the only distinction that matters is "did *I* commit
to this, or did someone else?" — and that is exactly what this gives us.

Without an enrolled voiceprint or without the embedding model present, labels stay
``unknown`` and the distiller infers attribution from wording instead. Nothing
breaks; it just gets less certain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .asr import AsrSegment

log = logging.getLogger(__name__)

OWNER = "owner"
GUEST = "guest"
UNKNOWN = "unknown"


@dataclass(slots=True)
class SpeakerMatch:
    label: str
    confidence: float


class SpeakerEmbedder:
    """sherpa-onnx speaker-embedding wrapper (CAM++ / 3D-Speaker family).

    Kept optional on purpose: the model is a ~30 MB ONNX file that the operator
    drops into ``SS_SPEAKER_MODEL_DIR``. If it is absent we degrade to
    ``unknown`` rather than failing the job.
    """

    def __init__(self, settings) -> None:
        self.s = settings
        self._extractor = None
        self._model_name = ""
        self._failed = False

    @property
    def available(self) -> bool:
        return self._resolve() is not None

    def _resolve(self) -> Path | None:
        if self._extractor is not None:
            return Path(self._model_name)
        if self._failed:
            return None
        try:
            import sherpa_onnx  # noqa: F401
        except Exception as exc:
            log.info("speaker embedding unavailable (sherpa-onnx not installed: %s)", exc)
            self._failed = True
            return None

        candidates: list[Path] = []
        if self.s.speaker_model_dir:
            root = Path(self.s.speaker_model_dir)
            candidates += sorted(root.glob("*.onnx"))
        candidates += sorted(self.s.models_dir.glob("speaker/*.onnx"))
        if not candidates:
            log.info("speaker embedding model not found; speaker labels will be 'unknown'")
            self._failed = True
            return None
        return candidates[0]

    def _lazy(self):
        if self._extractor is not None:
            return self._extractor
        path = self._resolve()
        if path is None:
            return None
        import sherpa_onnx

        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=2)
        if not cfg.validate():
            log.warning("speaker model %s failed validation; disabling embeddings", path)
            self._failed = True
            return None
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self._model_name = str(path)
        log.info("speaker embedding model loaded: %s (dim=%d)", path.name, self._extractor.dim)
        return self._extractor

    @property
    def dim(self) -> int:
        ex = self._lazy()
        return int(ex.dim) if ex else 0

    def embed_pcm(self, samples: np.ndarray, sample_rate: int = 16_000) -> np.ndarray | None:
        """Embed float32 mono samples in [-1, 1]."""
        ex = self._lazy()
        if ex is None:
            return None
        try:
            stream = ex.create_stream()
            stream.accept_waveform(sample_rate, samples.astype(np.float32))
            stream.input_finished()
            if not ex.is_ready(stream):
                return None
            vec = np.asarray(ex.compute_embedding(stream), dtype=np.float32)
        except Exception as exc:
            log.warning("speaker embedding failed: %s", exc)
            return None
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 1e-6 else vec

    def embed_wav(self, wav: Path, start_ms: int = 0, end_ms: int = 0) -> np.ndarray | None:
        """Embed a slice of a 16 kHz mono WAV."""
        import wave

        try:
            with wave.open(str(wav), "rb") as fh:
                rate = fh.getframerate()
                total = fh.getnframes()
                first = max(0, int(start_ms / 1000 * rate)) if end_ms else 0
                last = min(total, int(end_ms / 1000 * rate)) if end_ms else total
                if last - first < rate * 0.4:  # need ~0.4 s minimum for a usable vector
                    return None
                fh.setpos(first)
                raw = fh.readframes(last - first)
        except Exception as exc:
            log.debug("could not read WAV slice from %s: %s", wav, exc)
            return None

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return self.embed_pcm(samples, rate)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return -1.0
    return float(np.dot(a, b))


class SpeakerLabeler:
    """Applies enrolled voiceprints to ASR segments."""

    def __init__(self, settings, embedder: SpeakerEmbedder | None = None) -> None:
        self.s = settings
        self.embedder = embedder or SpeakerEmbedder(settings)
        self.owner_vector: np.ndarray | None = None
        self.owner_label = "主人"

    # ------------------------------------------------------------- enrollment
    def load_owner(self, embedding: np.ndarray | None, label: str = "主人") -> None:
        self.owner_vector = embedding
        self.owner_label = label

    def enroll_from_wav(self, wav: Path) -> np.ndarray | None:
        vec = self.embedder.embed_wav(wav)
        if vec is not None:
            self.owner_vector = vec
        return vec

    # -------------------------------------------------------------- labelling
    def label(self, wav: Path, segments: list[AsrSegment]) -> list[SpeakerMatch]:
        if self.owner_vector is None or not self.embedder.available:
            return [SpeakerMatch(UNKNOWN, 0.0) for _ in segments]

        # A single embedding over the whole file is a cheap, robust prior for
        # single-speaker captures, and a reasonable fallback for short segments.
        out: list[SpeakerMatch] = []
        for seg in segments:
            vec = self.embedder.embed_wav(wav, seg.start_ms, seg.end_ms)
            if vec is None:
                out.append(SpeakerMatch(UNKNOWN, 0.0))
                continue
            score = cosine(vec, self.owner_vector)
            out.append(
                SpeakerMatch(OWNER if score >= self.s.owner_threshold else GUEST, round(score, 4))
            )
        return out


def summary(matches: list[SpeakerMatch]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in matches:
        counts[m.label] = counts.get(m.label, 0) + 1
    return counts
