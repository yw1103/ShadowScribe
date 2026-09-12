"""SQLite engine + session helpers.

The API and the worker run in separate containers against one SQLite file, so we
lean on WAL mode and a generous busy timeout instead of a separate database
server. That keeps the MVP a single-volume deploy with zero extra services.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

_engine = None


def get_engine(db_path: Path | None = None):
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        from .config import settings

        path = Path(db_path) if db_path else settings.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{path}",
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - driver callback
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return _engine


def init_db(db_path: Path | None = None) -> None:
    """Create tables. Importing the model modules registers them on the metadata."""
    from . import models  # noqa: F401  (side effect: table registration)
    from .memory import native  # noqa: F401  (side effect: fallback-store tables)

    SQLModel.metadata.create_all(get_engine(db_path))


@contextmanager
def session_scope(db_path: Path | None = None) -> Iterator[Session]:
    """Transactional session. Commits on success, rolls back on exception."""
    with Session(get_engine(db_path)) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def reset_engine() -> None:
    """Test hook: drop the cached engine so a new ``SS_DATA_DIR`` takes effect."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
