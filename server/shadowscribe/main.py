"""ASGI entrypoint.

``uvicorn shadowscribe.main:app`` — the API container runs this. Heavy pipeline
work lives in :mod:`shadowscribe.worker` so a long transcription never blocks a
phone upload or a desktop ``ss brief``.
"""

from __future__ import annotations

import logging
import sys

from .api import create_app
from .config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stderr,
)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = create_app()


def main() -> None:
    """Run the API with uvicorn (used by the ``shadowscribe serve`` command)."""
    import uvicorn

    if settings.dev_token_generated:
        banner = (
            "\n" + "=" * 68 + "\n"
            "  SS_TOKEN is not set — dev mode.\n"
            f"  Bearer token for this process: {settings.effective_token}\n"
            "  Set SS_TOKEN in .env before exposing this port.\n" + "=" * 68
        )
        print(banner, file=sys.stderr, flush=True)

    uvicorn.run(
        "shadowscribe.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
