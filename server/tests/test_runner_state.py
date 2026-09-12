"""Pipeline-level tests for state that must not be cached wrongly.

Both cases here came from live runs, not review: a worker that kept a stale
voiceprint, and the fact that the fix has to be observable without a restart.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlmodel import Session, select

from shadowscribe import models
from shadowscribe.config import settings as default_settings
from shadowscribe.pipeline.runner import Pipeline


class SilentLabeler:
    """Records what the pipeline asked it to adopt, without touching a model."""

    def __init__(self):
        self.vector = "unset"
        self.label = ""

    def load_owner(self, embedding, label="主人"):
        self.vector = None if embedding is None else np.asarray(embedding)
        self.label = label


def make_pipeline(*, diarization="embedding") -> Pipeline:
    """A Pipeline with every heavy model stubbed out."""
    s = default_settings.model_copy(update={"diarization": diarization})
    pipe = Pipeline.__new__(Pipeline)
    pipe.s = s
    pipe.backend = None
    pipe.transcriber = None
    pipe.embedder = None
    pipe.labeler = SilentLabeler()
    pipe.distiller = None
    pipe._owner_key = None
    return pipe


def enroll(engine, label="主人", vector=(1.0, 0.0, 0.0)) -> models.Speaker:
    """Replace the enrolled owner, exactly like POST /v1/speakers/enroll does."""
    with Session(engine) as session:
        for old in session.exec(select(models.Speaker)).all():
            if old.is_owner:
                session.delete(old)
        row = models.Speaker(
            label=label,
            is_owner=True,
            embedding=np.asarray(vector, dtype=np.float32).tobytes(),
            dim=len(vector),
            model="fake",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def load(pipe: Pipeline, engine) -> None:
    with Session(engine) as session:
        pipe._load_owner(session)


def test_owner_voiceprint_is_adopted(clean_db):
    pipe = make_pipeline()
    row = enroll(clean_db)

    load(pipe, clean_db)

    assert pipe.labeler.vector is not None
    assert pipe.labeler.label == row.label
    assert pipe._owner_key == (row.id, row.created_at)


def test_re_enrolment_is_picked_up_without_a_restart(clean_db):
    """Enrol A, run, enrol B, run again — the second run must use B.

    Live symptom: after re-enrolling, similarity scores came back byte-identical
    to the previous run, i.e. the worker was still comparing against the old
    vector and the user had no way to tell except by restarting the container.
    """
    pipe = make_pipeline()

    enroll(clean_db, label="旧嗓音", vector=(1.0, 0.0, 0.0))
    load(pipe, clean_db)
    first = pipe.labeler.vector.copy()
    assert pipe.labeler.label == "旧嗓音"

    enroll(clean_db, label="新嗓音", vector=(0.0, 1.0, 0.0))
    load(pipe, clean_db)

    assert pipe.labeler.label == "新嗓音"
    assert not np.allclose(first, pipe.labeler.vector), "still using the previous voiceprint"


def test_repeated_loads_do_not_re_read(clean_db):
    """Cheapness matters: this runs once per recording."""
    pipe = make_pipeline()
    enroll(clean_db)

    load(pipe, clean_db)
    marker = pipe.labeler.vector
    load(pipe, clean_db)
    assert pipe.labeler.vector is marker


def test_missing_voiceprint_degrades_and_clears_a_previous_one(clean_db):
    pipe = make_pipeline()
    enroll(clean_db)
    load(pipe, clean_db)
    assert pipe.labeler.vector is not None

    with Session(clean_db) as session:
        for row in session.exec(select(models.Speaker)).all():
            session.delete(row)
        session.commit()

    load(pipe, clean_db)

    assert pipe.labeler.vector is None
    assert pipe._owner_key is None


def test_diarization_off_never_loads(clean_db):
    """With diarization off every segment stays unknown; loading is pointless work."""
    pipe = make_pipeline(diarization="off")
    enroll(clean_db)

    load(pipe, clean_db)

    assert pipe.labeler.vector == "unset"
    assert pipe._owner_key is None


def test_reload_owner_forces_a_re_read(clean_db):
    pipe = make_pipeline()
    enroll(clean_db)
    load(pipe, clean_db)
    assert pipe._owner_key is not None

    pipe.reload_owner()
    assert pipe._owner_key is None


@pytest.mark.parametrize("vector", [(1.0, 0.0), (0.1, 0.2, 0.3, 0.4)])
def test_voiceprint_dimension_is_preserved(clean_db, vector):
    pipe = make_pipeline()
    row = enroll(clean_db, vector=vector)
    load(pipe, clean_db)
    assert pipe.labeler.vector.shape == (len(vector),)
    assert row.dim == len(vector)
