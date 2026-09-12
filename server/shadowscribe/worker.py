"""Background worker.

A single long-lived process that owns the expensive models (Whisper, speaker
embedding) and drains the job table. Kept intentionally dumb: poll, claim, run,
record the outcome. No broker, no scheduler — the SQLite table *is* the queue,
which is why the MVP deploys as one volume instead of a cluster.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from sqlmodel import Session, select

from . import models
from .config import settings
from .db import get_engine, init_db
from .pipeline.runner import Pipeline

log = logging.getLogger(__name__)

_running = True


def _install_signal_handlers() -> None:
    def _stop(signum, _frame):  # pragma: no cover - signal path
        global _running
        log.info("received signal %s; finishing current job then exiting", signum)
        _running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def recover_stale_jobs() -> int:
    """Requeue jobs left ``running`` by a crashed worker."""
    with Session(get_engine()) as session:
        stale = session.exec(select(models.Job).where(models.Job.status == "running")).all()
        for job in stale:
            job.status = "queued"
            job.error = "requeued after worker restart"
            session.add(job)
        if stale:
            log.warning("requeued %d job(s) left running by a previous worker", len(stale))
        session.commit()
        return len(stale)


def claim_next_job() -> models.Job | None:
    """Atomically claim the oldest queued job."""
    engine = get_engine()
    with Session(engine) as session:
        job = session.exec(
            select(models.Job)
            .where(models.Job.status == "queued")
            .order_by(models.Job.priority.desc(), models.Job.created_at)
            .limit(1)
        ).first()
        if job is None:
            return None
        job.status = "running"
        job.started_at = _now()
        job.attempts += 1
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def _finish(job_id: str, *, status: str, result: dict | None = None, error: str | None = None) -> None:
    with Session(get_engine()) as session:
        job = session.get(models.Job, job_id)
        if job is None:
            return
        job.status = status
        job.error = error
        job.result = json.dumps(result, ensure_ascii=False) if result else None
        job.finished_at = _now()
        session.add(job)
        session.commit()


def run_job(pipeline: Pipeline, job: models.Job) -> None:
    payload: dict = {}
    if job.payload:
        try:
            payload = json.loads(job.payload)
        except json.JSONDecodeError:
            payload = {}

    log.info("job %s → recording %s (attempt %d)", job.id, job.recording_id, job.attempts)
    try:
        result = pipeline.process(
            job.recording_id, index_memory=payload.get("index_memory", True)
        )
    except Exception as exc:
        will_retry = job.attempts < job.max_attempts
        log.error(
            "job %s failed (attempt %d/%d)%s: %s",
            job.id,
            job.attempts,
            job.max_attempts,
            "; will retry" if will_retry else "; giving up",
            exc,
        )
        if will_retry:
            with Session(get_engine()) as session:
                row = session.get(models.Job, job.id)
                if row:
                    row.status = "queued"
                    row.error = f"{type(exc).__name__}: {exc}"[:1000]
                    session.add(row)
                    session.commit()
        else:
            _finish(job.id, status="failed", error=f"{type(exc).__name__}: {exc}"[:2000])
        return

    _finish(job.id, status="done", result=result)
    log.info("job %s done: %s", job.id, result)


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    once = "--once" in argv

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    settings.ensure_dirs()
    init_db()
    recover_stale_jobs()
    _install_signal_handlers()

    log.info(
        "worker up — backend=%s whisper=%s diarization=%s llm=%s",
        settings.memory_backend,
        settings.whisper_model,
        settings.diarization,
        settings.llm_model if settings.llm_enabled and settings.llm_api_key else "disabled",
    )

    pipeline = Pipeline()
    idle_logged = False
    try:
        while _running:
            job = claim_next_job()
            if job is None:
                if once:
                    log.info("no queued jobs; --once requested, exiting")
                    break
                if not idle_logged:
                    log.info("idle; polling every %.1fs", settings.worker_poll_interval_s)
                    idle_logged = True
                # Sleep in slices so SIGTERM is honoured promptly.
                deadline = time.monotonic() + settings.worker_poll_interval_s
                while _running and time.monotonic() < deadline:
                    time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                continue
            idle_logged = False
            run_job(pipeline, job)
            if once:
                break
    finally:
        pipeline.distiller.close()
        log.info("worker stopped")


if __name__ == "__main__":
    main()
