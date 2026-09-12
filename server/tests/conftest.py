"""Test bootstrap.

Environment has to be configured *before* ``shadowscribe`` is imported, because
``config.settings`` is a module-level singleton and several modules read it at
import time. That ordering is why this lives in ``conftest`` rather than a fixture.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="shadowscribe-tests-"))

os.environ.update(
    {
        "SS_DATA_DIR": str(_TMP),
        "SS_TOKEN": "test-token",
        # The unit suite must pass without the Rust wheel and without a network.
        "SS_MEMORY_BACKEND": "native",
        "SS_LLM_ENABLED": "false",
        "SS_LLM_API_KEY": "",
        "SS_DIARIZATION": "off",
        "SS_WHISPER_MODEL": "tiny",
        "SS_TIMEZONE": "Asia/Shanghai",
        "SS_LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}

#: A 0.25 s silent 16-bit mono WAV — a valid container with no speech in it.
SILENT_WAV = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return _TMP


@pytest.fixture()
def clean_db(data_dir: Path):
    """Fresh schema + empty tables for tests that assert on counts."""
    from sqlmodel import Session, delete

    from shadowscribe import models
    from shadowscribe.db import get_engine, init_db
    from shadowscribe.memory import native

    init_db()
    engine = get_engine()
    with Session(engine) as session:
        for model in (
            models.Segment,
            models.Commitment,
            models.Entity,
            models.Episode,
            models.Job,
            models.Recording,
            models.UploadSession,
            models.Speaker,
            native.MemEdge,
            native.MemFact,
        ):
            session.exec(delete(model))
        session.commit()
    return engine


@pytest.fixture()
def client(clean_db):
    from fastapi.testclient import TestClient

    from shadowscribe.api import create_app

    with TestClient(create_app()) as c:
        yield c
