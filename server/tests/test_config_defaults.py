"""Defaults must be valid without any environment override.

Why this file exists: a careless edit once deleted the newline between
``memory_backend``'s default and its docstring, so Python concatenated them and the
default became ``"causal-memory``causal-memory`` (recommended…)"``. ``ruff format``
then rewrapped it into an innocent-looking parenthesised expression.

Nothing caught it. ``conftest`` sets ``SS_MEMORY_BACKEND=native`` for the rest of
the suite, so the broken default was never exercised locally or in CI — it
surfaced only when the Docker image asserted that the package imports. These tests
build ``Settings`` from a scrubbed environment so a bad default fails here instead.
"""

from __future__ import annotations

import pytest

from shadowscribe.config import Settings

#: Every SS_* key conftest sets, so the defaults underneath can be observed.
_SS_VARS = (
    "SS_DATA_DIR",
    "SS_TOKEN",
    "SS_MEMORY_BACKEND",
    "SS_LLM_ENABLED",
    "SS_LLM_API_KEY",
    "SS_DIARIZATION",
    "SS_WHISPER_MODEL",
    "SS_TIMEZONE",
    "SS_LOG_LEVEL",
)


@pytest.fixture()
def clean_env(monkeypatch):
    for name in _SS_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_construct_with_pure_defaults(clean_env):
    """The whole point: a fresh install with an empty .env must boot."""
    s = Settings(_env_file=None)
    assert s.memory_backend in {"causal-memory", "native"}
    assert s.diarization in {"off", "embedding"}
    assert s.timezone


def test_enum_defaults_are_exactly_the_value_not_a_concatenation(clean_env):
    """Adjacent string literals concatenate silently; assert the exact strings."""
    s = Settings(_env_file=None)
    assert s.memory_backend == "causal-memory"
    assert s.diarization == "off"
    assert s.whisper_language == "zh"


def test_defaults_are_not_accidentally_python_docstrings(clean_env):
    """No default should contain text from its own documentation."""
    s = Settings(_env_file=None)
    for field in ("memory_backend", "diarization", "llm_model", "timezone"):
        value = getattr(s, field)
        assert "``" not in value, f"{field} default swallowed its docstring: {value!r}"
        assert "recommended" not in value, f"{field} default swallowed its docstring: {value!r}"
        assert len(value) < 60, f"{field} default looks concatenated: {value!r}"


def test_paths_derive_from_data_dir(clean_env, tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.audio_dir == tmp_path / "audio"
    assert s.raw_dir == tmp_path / "raw"
    assert s.db_path == tmp_path / "shadowscribe.db"
    assert s.resolved_memory_db() == tmp_path / "memory" / "causal.db"
