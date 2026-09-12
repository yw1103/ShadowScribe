"""Sensory pipeline — the "ears".

Nothing in this package ever produces audio output, a notification or a
suggestion. It only reads the world and writes structured meaning. That
constraint (原则一: 绝对静默旁听) is why the modules here are pure functions of
audio in, structure out.
"""

from __future__ import annotations

from .asr import AsrSegment, Transcriber
from .audio import AudioError, normalize, probe
from .distill import Distiller, DistillationResult
from .runner import Pipeline

__all__ = [
    "AsrSegment",
    "AudioError",
    "DistillationResult",
    "Distiller",
    "Pipeline",
    "Transcriber",
    "normalize",
    "probe",
]
