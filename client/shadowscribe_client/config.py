"""Client configuration.

Resolution order (first hit wins):

1. explicit CLI flags (``--endpoint`` / ``--token``)
2. environment (``SS_ENDPOINT`` / ``SS_TOKEN`` / ``SS_CONFIG``)
3. ``~/.shadowscribe/config.json``
4. ``./.shadowscribe.json`` (per-project, handy for teams pointing at one server)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_ENDPOINT = "http://127.0.0.1:18080"


@dataclass
class ClientConfig:
    endpoint: str = DEFAULT_ENDPOINT
    token: str = ""
    timeout_s: float = 60.0
    default_hours: int = 24
    default_max_tokens: int = 1800

    # ------------------------------------------------------------------ paths
    @staticmethod
    def home() -> Path:
        override = os.environ.get("SS_CONFIG")
        if override:
            return Path(override).expanduser()
        return Path.home() / ".shadowscribe" / "config.json"

    @staticmethod
    def project_file() -> Path:
        return Path.cwd() / ".shadowscribe.json"

    # ------------------------------------------------------------------- load
    @classmethod
    def load(cls, *, endpoint: str | None = None, token: str | None = None) -> ClientConfig:
        cfg = cls()
        for path in (cls.home(), cls.project_file()):
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    for key, value in data.items():
                        if hasattr(cfg, key) and value not in (None, ""):
                            setattr(cfg, key, value)
                except (json.JSONDecodeError, OSError):
                    continue

        if os.environ.get("SS_ENDPOINT"):
            cfg.endpoint = os.environ["SS_ENDPOINT"]
        if os.environ.get("SS_TOKEN"):
            cfg.token = os.environ["SS_TOKEN"]
        if endpoint:
            cfg.endpoint = endpoint
        if token:
            cfg.token = token
        return cfg

    def save(self) -> Path:
        path = self.home()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)  # holds a bearer token
        except OSError:
            pass
        return path
