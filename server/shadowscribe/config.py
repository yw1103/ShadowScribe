"""Runtime configuration.

Everything is driven by ``SS_*`` environment variables so the same image can run as
an API container, a worker container, or a laptop dev server. See ``.env.example``
for the annotated list.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Filled on first access when ``SS_TOKEN`` is unset, so a laptop run still has
#: *some* gate instead of a wide-open port.
_EPHEMERAL_TOKEN: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- storage
    data_dir: Path = Path("/data")
    """Root for audio, models and the SQLite database. Mount a volume here."""

    keep_audio: bool = True
    """Keep the normalised WAV after processing. Set false to delete once distilled."""

    audio_retention_days: int = 30
    """Age at which archived audio is pruned by ``shadowscribe.cli prune``."""

    # -------------------------------------------------------------------- api
    host: str = "0.0.0.0"
    port: int = 18080
    token: str = ""
    """Bearer token required by every ``/v1`` route. Empty means *dev mode*:
    generated on the fly at boot and printed to stderr so a laptop run stays easy."""

    max_upload_mb: int = 2048
    cors_origins: str = "*"

    mcp_require_token: bool = False
    """Gate ``/mcp`` behind the bearer token. Off for the MVP: this is a
    single-user tool on the owner's own machine, and a header in an editor config
    buys nothing yet. Turn it on before the endpoint stops being private."""

    # -------------------------------------------------------------------- asr
    whisper_model: str = "small"
    """faster-whisper model name or local path. ``small`` boots fast; ``large-v3``
    is materially better on Chinese and is the recommended steady state."""

    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str = "zh"
    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True
    whisper_initial_prompt: str = ""
    """Priming text; helps with domain jargon and proper nouns."""

    hf_endpoint: str = "https://hf-mirror.com"
    """Model hub mirror. Defaults to hf-mirror so mainland hosts can download."""

    hf_disable_xet: bool = True
    """Force classic HTTP downloads instead of HuggingFace's Xet protocol.

    Newer ``huggingface_hub`` releases route large files through Xet
    (``cas-server.xethub.hf.co``), which mirrors such as hf-mirror.com do not
    proxy — the download then fails with an opaque 401. Leaving this on is what
    makes model bootstrap actually work in mainland China.
    """

    asr_cpu_threads: int = 0
    """0 = let ctranslate2 decide (uses every core)."""

    simplify_chinese: bool = True
    """Normalise Traditional → Simplified after ASR. Retrieval is substring based,
    so mixed orthography silently splits one memory into two. No-op if OpenCC is
    not installed."""

    # ------------------------------------------------------------- diarization
    diarization: str = "off"
    """``off`` | ``embedding``. ``embedding`` enables voiceprint-based owner/guest
    labelling and needs the speaker-embedding model to be present."""

    speaker_model_dir: str = ""
    """Directory holding the sherpa-onnx speaker-embedding ONNX model."""

    owner_threshold: float = 0.55
    """Cosine similarity above which a segment is attributed to the owner."""

    # ------------------------------------------------------------ distillation
    llm_enabled: bool = True
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.1
    llm_timeout_s: int = 240
    llm_max_retries: int = 3

    distill_window_s: int = 720
    """Transcript seconds per distillation call. Long days are chunked."""

    distill_min_chars: int = 40
    """Skip windows shorter than this — usually silence or a door slamming."""

    # ----------------------------------------------------------------- memory
    memory_backend: str = "causal-memory"
    """``causal-memory`` (recommended, uses the causal-memory PyO3 bindings) or
    ``native`` (built-in SQLite fallback so the stack still runs standalone)."""

    memory_db: str = ""
    """Path to the causal-memory SQLite store. Empty → ``<data_dir>/memory/causal.db``."""

    memory_llm_extraction: bool = False
    """Let causal-memory run its own LLM extraction on top of ours. Off by default:
    ShadowScribe already distils, and double extraction doubles cost."""

    # ------------------------------------------------------------------- misc
    timezone: str = "Asia/Shanghai"
    """Operator timezone. Decides what "今天/周四/下周一" resolves to during
    distillation, and which calendar day an episode lands on. Needs the ``tzdata``
    package on Windows and slim container images; without it we fall back to UTC."""

    log_level: str = "INFO"
    worker_poll_interval_s: float = 2.0
    worker_max_attempts: int = 3

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        """Reject typos, but tolerate a host with no tz database at all.

        These are different failures: ``Asia/Shangai`` is a mistake worth failing
        fast on, whereas a Windows box or slim image without ``tzdata`` should
        degrade to UTC rather than refuse to boot.
        """
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(v)
        except Exception as exc:
            try:
                ZoneInfo("UTC")
            except Exception:
                logging.getLogger(__name__).warning(
                    "no IANA time zone database on this host; SS_TIMEZONE=%r will be "
                    "treated as UTC. Install the `tzdata` package to fix dates.",
                    v,
                )
                return v
            raise ValueError(f"SS_TIMEZONE={v!r} is not a known IANA zone") from exc
        return v

    # ------------------------------------------------------------ validators
    @field_validator("diarization")
    @classmethod
    def _check_diarization(cls, v: str) -> str:
        allowed = {"off", "embedding"}
        if v not in allowed:
            raise ValueError(f"SS_DIARIZATION must be one of {sorted(allowed)}, got {v!r}")
        return v

    @field_validator("memory_backend")
    @classmethod
    def _check_backend(cls, v: str) -> str:
        allowed = {"causal-memory", "native"}
        if v not in allowed:
            raise ValueError(f"SS_MEMORY_BACKEND must be one of {sorted(allowed)}, got {v!r}")
        return v

    # ---------------------------------------------------------------- helpers
    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shadowscribe.db"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    def resolved_memory_db(self) -> Path:
        return Path(self.memory_db) if self.memory_db else self.memory_dir / "causal.db"

    @property
    def effective_token(self) -> str:
        """Token actually enforced. Generates an ephemeral one in dev mode."""
        if self.token:
            return self.token
        global _EPHEMERAL_TOKEN
        if not _EPHEMERAL_TOKEN:
            _EPHEMERAL_TOKEN = secrets.token_urlsafe(24)
        return _EPHEMERAL_TOKEN

    @property
    def dev_token_generated(self) -> bool:
        return not self.token

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.audio_dir, self.raw_dir, self.models_dir, self.memory_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
