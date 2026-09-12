"""Speaker-attribution tests.

Two things this guards, both found on a live deployment rather than in review:

* ``sherpa-onnx`` 1.13 renamed ``compute_embedding()`` to ``compute()``. The old
  call raised AttributeError deep inside an except-block that swallowed it, so
  every enrolment returned "audio too short or too noisy" and every segment was
  labelled unknown — a broken voiceprint looked like bad audio.
* numpy is optional, so none of this may be a hard dependency of importing the
  pipeline.
"""

from __future__ import annotations

import numpy as np
import pytest

from shadowscribe.pipeline import diarize
from shadowscribe.pipeline.diarize import GUEST, OWNER, UNKNOWN, SpeakerEmbedder, SpeakerLabeler


class FakeStream:
    def __init__(self) -> None:
        self.samples = None

    def accept_waveform(self, rate, samples):
        self.samples = np.asarray(samples)

    def input_finished(self):
        pass


class FakeExtractorOld:
    """sherpa-onnx < 1.13: compute_embedding(stream)."""

    dim = 4

    def __init__(self, vector):
        self.vector = vector

    def create_stream(self):
        return FakeStream()

    def is_ready(self, _stream):
        return True

    def compute_embedding(self, _stream):
        return self.vector


class FakeExtractorNew:
    """sherpa-onnx >= 1.13: compute(stream)."""

    dim = 4

    def __init__(self, vector):
        self.vector = vector

    def create_stream(self):
        return FakeStream()

    def is_ready(self, _stream):
        return True

    def compute(self, _stream):
        return self.vector


class FakeExtractorNeither:
    dim = 4

    def create_stream(self):
        return FakeStream()

    def is_ready(self, _stream):
        return True


@pytest.mark.parametrize("cls", [FakeExtractorOld, FakeExtractorNew])
def test_compute_embedding_resolves_across_versions(cls):
    vector = [3.0, 4.0, 0.0, 0.0]
    out = diarize._compute_embedding(cls(vector), FakeStream())
    assert np.allclose(out, vector)


def test_compute_embedding_raises_on_an_unknown_version():
    with pytest.raises(AttributeError, match="neither"):
        diarize._compute_embedding(FakeExtractorNeither(), FakeStream())


def test_embed_pcm_normalises_the_vector(monkeypatch):
    """Cosine similarity is only meaningful on unit vectors."""

    class Embedder(SpeakerEmbedder):
        def _lazy(self):
            return FakeExtractorNew([3.0, 4.0, 0.0, 0.0])

    vec = Embedder.__new__(Embedder)
    vec.s = type("S", (), {"models_dir": None, "speaker_model_dir": ""})()
    vec._extractor = None
    vec._model_name = ""
    vec._failed = False

    out = SpeakerEmbedder.embed_pcm(vec, np.zeros(16000, dtype=np.float32), 16000)
    assert out is not None
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-5


def test_embed_pcm_reports_when_there_is_not_enough_audio(caplog):
    class NotReady(FakeExtractorNew):
        def is_ready(self, _stream):
            return False

    class Embedder(SpeakerEmbedder):
        def _lazy(self):
            return NotReady([1.0, 0.0])

    emb = SpeakerEmbedder.__new__(Embedder)
    emb.s = type("S", (), {"models_dir": None, "speaker_model_dir": ""})()
    emb._extractor = None
    emb._model_name = ""
    emb._failed = False

    with caplog.at_level("WARNING"):
        assert SpeakerEmbedder.embed_pcm(emb, np.zeros(100, dtype=np.float32), 16000) is None
    # silence about this is what hid the API rename for a whole debugging round
    assert any("not enough" in r.message for r in caplog.records)


def test_cosine_is_minus_one_for_unusable_input():
    assert diarize.cosine(None, None) == -1.0
    assert diarize.cosine(np.zeros(0), np.zeros(0)) == -1.0
    assert diarize.cosine(np.zeros(3), np.zeros(4)) == -1.0


def test_cosine_matches_hand_computation():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.6, 0.8], dtype=np.float32)
    assert diarize.cosine(a, b) == pytest.approx(0.6)


# ------------------------------------------------------------------- labelling


class FakeLabeler(SpeakerLabeler):
    """Labeler with a scripted embedder, so no model is needed."""

    def __init__(self, owner, per_segment, threshold=0.55):
        self.s = type("S", (), {"owner_threshold": threshold})()
        self.owner_vector = np.asarray(owner, dtype=np.float32)
        self.owner_label = "主人"
        self.embedder = self
        self._per_segment = list(per_segment)

    def available(self):  # pragma: no cover - attribute shim
        return True

    def embed_wav(self, _wav, _start=0, _end=0):
        return self._per_segment.pop(0) if self._per_segment else None


def _segments(n):
    from shadowscribe.pipeline.asr import AsrSegment

    return [AsrSegment(start_ms=i * 1000, end_ms=(i + 1) * 1000, text=f"第{i}句") for i in range(n)]


def test_labels_owner_and_guest_by_threshold():
    labeler = FakeLabeler(
        owner=[1.0, 0.0],
        per_segment=[
            np.array([1.0, 0.0], dtype=np.float32),  # identical → owner
            np.array([0.0, 1.0], dtype=np.float32),  # orthogonal → guest
        ],
    )
    matches = labeler.label(None, _segments(2))
    assert [m.label for m in matches] == [OWNER, GUEST]
    assert matches[0].confidence == pytest.approx(1.0)
    assert matches[1].confidence == pytest.approx(0.0)


def test_unembeddable_segment_falls_back_to_unknown():
    labeler = FakeLabeler(owner=[1.0, 0.0], per_segment=[None])
    assert labeler.label(None, _segments(1))[0].label == UNKNOWN


def test_without_an_enrolled_owner_everything_is_unknown():
    """No voiceprint must degrade to 'unknown', never to a confident guess."""
    settings = type("S", (), {"owner_threshold": 0.55})()
    labeler = SpeakerLabeler(settings, embedder=SpeakerEmbedder(settings))
    labeler.owner_vector = None
    assert {m.label for m in labeler.label(None, _segments(3))} == {UNKNOWN}
