"""Persistent state for the sensory layer.

These tables are ShadowScribe's own bookkeeping — what audio arrived, how far the
pipeline got, and what was actually said. Long-term *meaning* lives in the memory
backend (see :mod:`shadowscribe.memory`), not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import Field, Index, SQLModel


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Recording(SQLModel, table=True):
    """One uploaded audio blob — typically one chunk of a phone's day-long capture."""

    __tablename__ = "recordings"

    id: str = Field(default_factory=new_id, primary_key=True)
    client_id: str = Field(default="unknown", index=True)
    device: str | None = None
    session_hint: str | None = None
    """Free-text context from the recorder, e.g. "与老王在会议室"."""

    recorded_at: datetime = Field(default_factory=now_utc, index=True)
    received_at: datetime = Field(default_factory=now_utc, index=True)

    duration_ms: int | None = None
    size_bytes: int = 0
    sha256: str = Field(default="", index=True)
    original_filename: str | None = None
    content_type: str | None = None

    raw_path: str | None = None
    audio_path: str | None = None
    """16 kHz mono WAV, the canonical artefact every later stage reads."""

    chunk_index: int | None = None
    chunk_total: int | None = None
    upload_id: str | None = Field(default=None, index=True)

    status: str = Field(default="queued", index=True)
    """queued | processing | done | failed | skipped"""

    error: str | None = None
    language: str | None = None
    speech_ms: int = 0
    processed_at: datetime | None = None

    def transcript_duration_s(self) -> float:
        return (self.duration_ms or 0) / 1000.0


class Job(SQLModel, table=True):
    """A unit of asynchronous work. The worker claims rows in FIFO order."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "created_at"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    recording_id: str = Field(index=True)
    stage: str = "pipeline"
    """pipeline (whole chain) | distill_only | reindex"""

    status: str = Field(default="queued", index=True)
    """queued | running | done | failed"""

    attempts: int = 0
    max_attempts: int = 3
    priority: int = 0
    error: str | None = None
    result: str | None = None
    payload: str | None = None

    created_at: datetime = Field(default_factory=now_utc, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class UploadSession(SQLModel, table=True):
    """Resumable chunked upload — for phone clients pushing multi-hundred-MB files."""

    __tablename__ = "upload_sessions"

    id: str = Field(default_factory=new_id, primary_key=True)
    client_id: str = "unknown"
    filename: str | None = None
    content_type: str | None = None
    total_parts: int | None = None
    received_parts: int = 0
    size_bytes: int = 0
    session_hint: str | None = None
    device: str | None = None
    recorded_at: datetime | None = None
    status: str = "open"
    """open | completed | aborted"""

    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class Segment(SQLModel, table=True):
    """One utterance from the ASR pass, with optional speaker attribution."""

    __tablename__ = "segments"
    __table_args__ = (Index("ix_segments_rec_start", "recording_id", "start_ms"),)

    id: int | None = Field(default=None, primary_key=True)
    recording_id: str = Field(index=True)
    idx: int = 0
    start_ms: int = 0
    end_ms: int = 0
    text: str = ""

    speaker: str | None = None
    """``owner`` | ``guest`` | ``unknown`` — see :mod:`shadowscribe.pipeline.diarize`."""

    speaker_confidence: float | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


class Speaker(SQLModel, table=True):
    """Enrolled voiceprint. Exactly one row is expected to carry ``is_owner``."""

    __tablename__ = "speakers"

    id: str = Field(default_factory=new_id, primary_key=True)
    label: str = Field(index=True)
    is_owner: bool = Field(default=False, index=True)
    embedding: bytes = b""
    dim: int = 0
    model: str = ""
    sample_seconds: float = 0.0
    created_at: datetime = Field(default_factory=now_utc)


class Episode(SQLModel, table=True):
    """A distilled causal episode: the unit a desktop agent actually reads."""

    __tablename__ = "episodes"

    id: str = Field(default_factory=new_id, primary_key=True)
    recording_id: str = Field(index=True)
    window_index: int = 0

    title: str = ""
    summary: str = ""
    topics: str = "[]"
    """JSON array of strings."""

    started_at: datetime = Field(default_factory=now_utc, index=True)
    ended_at: datetime | None = None
    speakers: str = "[]"

    cause_count: int = 0
    commitment_count: int = 0
    memory_ids: str = "[]"
    """JSON array of ids returned by the memory backend."""

    raw_json: str = ""
    created_at: datetime = Field(default_factory=now_utc, index=True)


class Commitment(SQLModel, table=True):
    """An explicit promise or todo. Surfaced directly by ``ss commitments``."""

    __tablename__ = "commitments"

    id: str = Field(default_factory=new_id, primary_key=True)
    episode_id: str = Field(index=True)
    recording_id: str = Field(index=True)

    what: str = ""
    owner: str = "我"
    counterparty: str | None = None
    due_text: str | None = None
    due_at: datetime | None = Field(default=None, index=True)
    status: str = Field(default="open", index=True)
    """open | done | cancelled"""

    confidence: float = 0.0
    evidence: str | None = None
    source_speaker: str | None = None
    created_at: datetime = Field(default_factory=now_utc, index=True)


class Entity(SQLModel, table=True):
    """Lightweight entity index so ``ss search 老王`` is cheap and predictable."""

    __tablename__ = "entities"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    kind: str = "unknown"
    """person | org | artifact | project | place | other"""

    episode_id: str = Field(index=True)
    recording_id: str = Field(index=True)
    aliases: str = "[]"
    mentions: int = 1
    created_at: datetime = Field(default_factory=now_utc)
