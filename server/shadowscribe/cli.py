"""Server-side admin CLI.

Installed as the ``shadowscribe`` console script. The *desktop* tool is a separate
package (``ss``) — this one only exists to operate the server box.

    shadowscribe serve            # API
    shadowscribe worker           # drain the queue
    shadowscribe ingest a.m4a     # push a local file through the pipeline (no phone needed)
    shadowscribe brief            # print the context card
    shadowscribe stats
    shadowscribe prune --days 30
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _configure_stdio() -> None:
    """Survive a non-UTF-8 console.

    The context card carries CJK plus emoji, and a Windows GBK console raises
    ``UnicodeEncodeError`` on the first one. Harmless on the Linux server, but a
    contributor running ``shadowscribe brief`` locally should not get a traceback.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                stream.reconfigure(errors="replace")


def _bootstrap_logging(level: str) -> None:
    import logging

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _cmd_serve(args) -> int:
    from .main import main as serve_main

    serve_main()
    return 0


def _cmd_worker(args) -> int:
    from .worker import main as worker_main

    argv = ["--once"] if args.once else []
    worker_main(argv)
    return 0


def _cmd_ingest(args) -> int:
    """Feed a local audio file through the pipeline — the fastest way to verify."""
    from sqlmodel import Session

    from . import models
    from .config import settings
    from .db import get_engine, init_db
    from .pipeline.runner import Pipeline

    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2

    settings.ensure_dirs()
    init_db()

    import hashlib
    import shutil

    sha = (
        hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stat().st_size < 512 * 1024 * 1024
        else ""
    )
    rec_id = models.new_id()
    target = settings.raw_dir / f"{rec_id}{path.suffix.lower() or '.bin'}"
    shutil.copy2(path, target)

    with Session(get_engine()) as session:
        rec = models.Recording(
            id=rec_id,
            client_id=args.client_id,
            device="cli",
            session_hint=args.hint,
            recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            size_bytes=target.stat().st_size,
            sha256=sha,
            original_filename=path.name,
            raw_path=str(target),
            status="queued",
        )
        session.add(rec)
        session.commit()
    print(f"recording {rec_id} created ({target.stat().st_size} bytes)")

    if args.enqueue:
        with Session(get_engine()) as session:
            job = models.Job(recording_id=rec_id, stage="pipeline", status="queued")
            session.add(job)
            session.commit()
        print(f"job {job.id} queued — run `shadowscribe worker` to process it")
        return 0

    pipeline = Pipeline()
    result = pipeline.process(rec_id, index_memory=not args.no_memory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_brief(args) -> int:
    from .service import BriefOptions, build_brief

    card = build_brief(
        BriefOptions(
            hours=args.hours,
            max_tokens=args.max_tokens,
            include_quotes=not args.no_quotes,
            include_entities=not args.no_entities,
        )
    )
    if args.out:
        Path(args.out).write_text(card, encoding="utf-8")
        print(f"wrote {args.out} ({len(card)} chars)")
    else:
        print(card)
    return 0


def _cmd_stats(args) -> int:
    from .config import settings
    from .db import init_db
    from .service import stats

    init_db()
    info = stats()
    info["config"] = {
        "data_dir": str(settings.data_dir),
        "memory_backend": settings.memory_backend,
        "whisper_model": settings.whisper_model,
        "diarization": settings.diarization,
        "llm_model": settings.llm_model if settings.llm_api_key else "(no key)",
        "llm_configured": bool(settings.llm_api_key),
        "timezone": settings.timezone,
    }
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def _cmd_prune(args) -> int:
    from .db import init_db
    from .pipeline.runner import Pipeline

    init_db()
    removed = Pipeline().prune_audio(args.days)
    print(f"removed {removed} audio file(s) older than {args.days} day(s)")
    return 0


def _cmd_doctor(args) -> int:
    """Check every external dependency and say precisely what is missing."""
    import shutil

    from .config import settings
    from .db import init_db
    from .pipeline.audio import ffmpeg_available
    from .pipeline.diarize import SpeakerEmbedder

    init_db()
    checks: list[tuple[str, bool, str]] = []

    checks.append(("ffmpeg", ffmpeg_available(), shutil.which("ffmpeg") or "not on PATH"))
    try:
        import faster_whisper  # noqa: F401

        checks.append(("faster-whisper", True, "importable"))
    except Exception as exc:
        checks.append(("faster-whisper", False, str(exc)))

    try:
        from .memory import get_backend

        backend = get_backend(settings)
        checks.append((f"memory backend ({backend.name})", True, str(backend.stats())[:120]))
    except Exception as exc:
        checks.append(("memory backend", False, str(exc)))

    llm_ok = bool(settings.llm_enabled and settings.llm_api_key)
    checks.append(
        (
            "LLM distillation",
            llm_ok,
            f"{settings.llm_model} @ {settings.llm_base_url}"
            if llm_ok
            else "SS_LLM_API_KEY not set",
        )
    )

    embedder = SpeakerEmbedder(settings)
    checks.append(
        (
            "speaker embedding",
            embedder.available,
            "ready" if embedder.available else "no sherpa-onnx model (SS_SPEAKER_MODEL_DIR)",
        )
    )

    writable = True
    try:
        settings.ensure_dirs()
        probe = settings.data_dir / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
    except Exception as exc:
        writable = False
        checks.append(("data dir writable", False, str(exc)))
    if writable:
        checks.append(("data dir writable", True, str(settings.data_dir)))

    width = max(len(name) for name, _, _ in checks)
    failures = 0
    for name, ok, detail in checks:
        mark = "OK  " if ok else "MISS"
        if not ok:
            failures += 1
        print(f"[{mark}] {name:<{width}}  {detail}")
    print()
    print(f"{len(checks) - failures}/{len(checks)} checks passed")
    return 0 if failures == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shadowscribe",
        description="影书 ShadowScribe — server administration",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the HTTP API")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("worker", help="run the pipeline worker")
    p.add_argument("--once", action="store_true", help="drain the queue and exit")
    p.set_defaults(func=_cmd_worker)

    p = sub.add_parser("ingest", help="push a local audio file through the pipeline")
    p.add_argument("path")
    p.add_argument("--hint", default=None, help="session hint, e.g. '与老王在会议室'")
    p.add_argument("--client-id", default="cli")
    p.add_argument("--enqueue", action="store_true", help="queue instead of processing now")
    p.add_argument("--no-memory", action="store_true", help="skip writing to the memory backend")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("brief", help="print the context card")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--max-tokens", type=int, default=1800)
    p.add_argument("--out", default=None, help="write to a file instead of stdout")
    p.add_argument("--no-quotes", action="store_true")
    p.add_argument("--no-entities", action="store_true")
    p.set_defaults(func=_cmd_brief)

    p = sub.add_parser("stats", help="store and configuration summary")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("prune", help="delete archived audio past retention")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=_cmd_prune)

    p = sub.add_parser("doctor", help="verify ffmpeg / whisper / memory / LLM readiness")
    p.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    _bootstrap_logging("WARNING")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
