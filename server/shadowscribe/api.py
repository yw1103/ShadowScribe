"""HTTP surface.

Two audiences, deliberately separated:

* **ingest** (``/v1/ingest/*``, ``/v1/uploads*``) — the phone. Write-only, tolerant
  of huge files, resumable. The phone should never need to be smart.
* **recall** (``/v1/context/*``, ``/v1/search``, ``/v1/commitments``, ``/v1/timeline``)
  — the desktop. Small, fast, markdown-shaped, meant to be pasted or MCP-proxied.

Every ``/v1`` route requires ``Authorization: Bearer <SS_TOKEN>`` unless the token
is empty (dev mode).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from sqlmodel import Session, desc, select

from . import __version__, models, service
from .config import settings
from .db import get_engine, init_db
from .memory import get_backend

log = logging.getLogger(__name__)

router = APIRouter()

_CHUNK = 1024 * 1024  # 1 MiB streaming copy


# ---------------------------------------------------------------------- auth


async def require_token(request: Request) -> None:
    expected = settings.effective_token
    if not expected:
        return
    header = request.headers.get("authorization", "")
    provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not provided:
        provided = request.headers.get("x-ss-token", "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


# ------------------------------------------------------------------ helpers


def _enqueue(session: Session, recording_id: str, *, stage: str = "pipeline") -> models.Job:
    job = models.Job(
        recording_id=recording_id,
        stage=stage,
        status="queued",
        max_attempts=settings.worker_max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def _sanitize_suffix(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix and len(suffix) <= 6 and suffix.isascii():
        return suffix
    return {
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "audio/amr": ".amr",
        "audio/3gpp": ".3gp",
    }.get((content_type or "").lower(), ".bin")


async def _stream_to(path: Path, chunks) -> tuple[int, str]:
    """Write an async chunk iterator to disk, returning ``(bytes, sha256)``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    limit = settings.max_upload_mb * 1024 * 1024
    with path.open("wb") as fh:
        async for chunk in chunks:
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                fh.close()
                path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"upload exceeds SS_MAX_UPLOAD_MB={settings.max_upload_mb}",
                )
            digest.update(chunk)
            fh.write(chunk)
    return total, digest.hexdigest()


def _existing_by_hash(session: Session, sha256: str) -> models.Recording | None:
    if not sha256:
        return None
    return session.exec(
        select(models.Recording)
        .where(models.Recording.sha256 == sha256)
        .where(models.Recording.status != "failed")
        .order_by(desc(models.Recording.received_at))
        .limit(1)
    ).first()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _finalize(
    session: Session,
    *,
    client_id: str,
    device: str | None,
    session_hint: str | None,
    recorded_at: datetime | None,
    filename: str | None,
    content_type: str | None,
    size: int,
    sha256: str,
    raw_path: Path,
    duration_ms: int | None,
    upload_id: str | None = None,
    chunk_index: int | None = None,
    chunk_total: int | None = None,
) -> dict:
    rec = models.Recording(
        client_id=client_id or "unknown",
        device=device,
        session_hint=session_hint,
        recorded_at=recorded_at or datetime.now(timezone.utc).replace(tzinfo=None),
        size_bytes=size,
        sha256=sha256,
        original_filename=filename,
        content_type=content_type,
        raw_path=str(raw_path),
        duration_ms=duration_ms,
        upload_id=upload_id,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        status="queued",
    )
    session.add(rec)
    session.flush()
    job = _enqueue(session, rec.id)
    session.commit()
    log.info("queued recording %s (%d bytes, client=%s)", rec.id, size, client_id)
    return {
        "recording_id": rec.id,
        "job_id": job.id,
        "status": rec.status,
        "dedup": False,
        "bytes": size,
    }


def _record_raw_path(recording_id: str, suffix: str) -> Path:
    return settings.raw_dir / f"{recording_id}{suffix or '.bin'}"


# ------------------------------------------------------------------- ingest


@router.post("/v1/ingest/audio", dependencies=[Depends(require_token)])
async def ingest_audio(
    file: UploadFile = File(..., description="any ffmpeg-decodable audio container"),
    client_id: str = Form("unknown"),
    device: str | None = Form(None),
    session_hint: str | None = Form(None),
    recorded_at: str | None = Form(None),
    duration_ms: int | None = Form(None),
    chunk_index: int | None = Form(None),
    chunk_total: int | None = Form(None),
):
    """Primary ingestion route for the phone client.

    Returns ``202``-style body on success. Re-uploading identical bytes is
    idempotent and returns the original ``recording_id`` with ``dedup: true``.
    """
    staging = settings.raw_dir / f".staging-{uuid.uuid4().hex}"
    size, sha256 = await _stream_to(staging, _iter_upload(file))

    with Session(get_engine()) as session:
        duplicate = _existing_by_hash(session, sha256)
        if duplicate is not None:
            staging.unlink(missing_ok=True)
            job = session.exec(
                select(models.Job)
                .where(models.Job.recording_id == duplicate.id)
                .order_by(desc(models.Job.created_at))
                .limit(1)
            ).first()
            return {
                "recording_id": duplicate.id,
                "job_id": job.id if job else None,
                "status": duplicate.status,
                "dedup": True,
                "bytes": size,
            }

        suffix = _sanitize_suffix(file.filename, file.content_type)
        rec_id = models.new_id()
        target = _record_raw_path(rec_id, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(target))

        return _finalize(
            session,
            client_id=client_id,
            device=device,
            session_hint=session_hint,
            recorded_at=_parse_dt(recorded_at),
            filename=file.filename,
            content_type=file.content_type,
            size=size,
            sha256=sha256,
            raw_path=target,
            duration_ms=duration_ms,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
        )


async def _iter_upload(file: UploadFile):
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        yield chunk


@router.post("/v1/ingest/raw", dependencies=[Depends(require_token)])
async def ingest_raw(
    request: Request,
    filename: str | None = Query(None),
    client_id: str = Query("unknown"),
    device: str | None = Query(None),
    session_hint: str | None = Query(None),
    recorded_at: str | None = Query(None),
    duration_ms: int | None = Query(None),
):
    """Body-only upload. Cheaper than multipart for large single files.

    ``curl --data-binary @day.m4a '.../v1/ingest/raw?client_id=pixel'``
    """
    staging = settings.raw_dir / f".staging-{uuid.uuid4().hex}"
    size, sha256 = await _stream_to(staging, _iter_request(request))

    with Session(get_engine()) as session:
        duplicate = _existing_by_hash(session, sha256)
        if duplicate is not None:
            staging.unlink(missing_ok=True)
            return {
                "recording_id": duplicate.id,
                "job_id": None,
                "status": duplicate.status,
                "dedup": True,
                "bytes": size,
            }

        suffix = _sanitize_suffix(
            filename or request.headers.get("x-ss-filename"),
            request.headers.get("content-type"),
        )
        rec_id = models.new_id()
        target = _record_raw_path(rec_id, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(target))

        return _finalize(
            session,
            client_id=client_id,
            device=device,
            session_hint=session_hint,
            recorded_at=_parse_dt(recorded_at),
            filename=filename,
            content_type=request.headers.get("content-type"),
            size=size,
            sha256=sha256,
            raw_path=target,
            duration_ms=duration_ms,
        )


async def _iter_request(request: Request):
    async for chunk in request.stream():
        yield chunk


# ------------------------------------------------------- resumable uploads


@router.post("/v1/uploads", dependencies=[Depends(require_token)])
def create_upload(
    filename: str | None = Query(None),
    total_parts: int | None = Query(None),
    client_id: str = Query("unknown"),
    device: str | None = Query(None),
    session_hint: str | None = Query(None),
    recorded_at: str | None = Query(None),
):
    """Open a chunked-upload session for a file too big for one request."""
    with Session(get_engine()) as session:
        up = models.UploadSession(
            client_id=client_id,
            filename=filename,
            total_parts=total_parts,
            device=device,
            session_hint=session_hint,
            recorded_at=_parse_dt(recorded_at),
        )
        session.add(up)
        session.commit()
        session.refresh(up)
        (settings.raw_dir / f"parts-{up.id}").mkdir(parents=True, exist_ok=True)
        return {"upload_id": up.id, "received_parts": 0, "total_parts": total_parts}


@router.put("/v1/uploads/{upload_id}/parts/{part_number}", dependencies=[Depends(require_token)])
async def put_part(upload_id: str, part_number: int, request: Request):
    """Upload one part. Re-uploading the same part number overwrites it safely."""
    with Session(get_engine()) as session:
        up = session.get(models.UploadSession, upload_id)
        if up is None:
            raise HTTPException(status_code=404, detail="unknown upload_id")
        if up.status != "open":
            raise HTTPException(status_code=409, detail=f"upload is {up.status}")

    parts_dir = settings.raw_dir / f"parts-{upload_id}"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_path = parts_dir / f"{part_number:06d}"

    size, _ = await _stream_to(part_path, _iter_request(request))

    with Session(get_engine()) as session:
        up = session.get(models.UploadSession, upload_id)
        if up is None:
            raise HTTPException(status_code=404, detail="unknown upload_id")
        up.received_parts = len([p for p in parts_dir.iterdir() if p.is_file()])
        up.size_bytes = sum(p.stat().st_size for p in parts_dir.iterdir() if p.is_file())
        up.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(up)
        session.commit()
        return {
            "upload_id": upload_id,
            "part": part_number,
            "bytes": size,
            "received_parts": up.received_parts,
            "total_parts": up.total_parts,
        }


@router.post("/v1/uploads/{upload_id}/complete", dependencies=[Depends(require_token)])
def complete_upload(upload_id: str):
    """Concatenate parts in numeric order and enqueue the recording."""
    parts_dir = settings.raw_dir / f"parts-{upload_id}"
    if not parts_dir.exists():
        raise HTTPException(status_code=404, detail="unknown upload_id")
    parts = sorted((p for p in parts_dir.iterdir() if p.is_file()), key=lambda p: p.name)
    if not parts:
        raise HTTPException(status_code=400, detail="no parts uploaded")

    with Session(get_engine()) as session:
        up = session.get(models.UploadSession, upload_id)
        if up is None:
            raise HTTPException(status_code=404, detail="unknown upload_id")
        if up.status != "open":
            raise HTTPException(status_code=409, detail=f"upload is {up.status}")
        if up.total_parts and len(parts) < up.total_parts:
            raise HTTPException(
                status_code=400,
                detail=f"expected {up.total_parts} parts, have {len(parts)}",
            )

        rec_id = models.new_id()
        suffix = _sanitize_suffix(up.filename, up.content_type)
        target = _record_raw_path(rec_id, suffix)
        digest = hashlib.sha256()
        total = 0
        with target.open("wb") as out:
            for part in parts:
                with part.open("rb") as fh:
                    while True:
                        buf = fh.read(_CHUNK)
                        if not buf:
                            break
                        digest.update(buf)
                        total += len(buf)
                        out.write(buf)

        up.status = "completed"
        up.size_bytes = total
        session.add(up)

        result = _finalize(
            session,
            client_id=up.client_id,
            device=up.device,
            session_hint=up.session_hint,
            recorded_at=up.recorded_at,
            filename=up.filename,
            content_type=up.content_type,
            size=total,
            sha256=digest.hexdigest(),
            raw_path=target,
            duration_ms=None,
            upload_id=upload_id,
        )
    shutil.rmtree(parts_dir, ignore_errors=True)
    return result


# ------------------------------------------------------------------ recall


@router.get("/v1/context/brief", response_class=PlainTextResponse, dependencies=[Depends(require_token)])
def context_brief(
    hours: int = Query(24, ge=1, le=24 * 30),
    since: str | None = Query(None, description="ISO-8601; overrides hours"),
    until: str | None = Query(None, description="ISO-8601"),
    max_tokens: int = Query(1800, ge=200, le=20000),
    quotes: bool = Query(True),
    entities: bool = Query(True),
):
    """The context card — paste this into any AI and it is already briefed."""
    opts = service.BriefOptions(
        hours=hours,
        since=_parse_dt(since),
        until=_parse_dt(until),
        max_tokens=max_tokens,
        include_quotes=quotes,
        include_entities=entities,
    )
    return service.build_brief(opts)


@router.get("/v1/commitments", dependencies=[Depends(require_token)])
def get_commitments(
    status: str = Query("open", pattern="^(open|done|cancelled|all)$"),
    limit: int = Query(50, ge=1, le=500),
):
    rows = service.list_commitments(status=status, limit=limit)
    return {
        "status": status,
        "count": len(rows),
        "commitments": [
            {
                "id": r.id,
                "what": r.what,
                "owner": r.owner,
                "counterparty": r.counterparty,
                "due_text": r.due_text,
                "due_at": r.due_at.isoformat() if r.due_at else None,
                "status": r.status,
                "confidence": r.confidence,
                "evidence": r.evidence,
                "recording_id": r.recording_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.patch("/v1/commitments/{commitment_id}", dependencies=[Depends(require_token)])
def patch_commitment(
    commitment_id: str, status: str = Query(..., pattern="^(open|done|cancelled)$")
):
    row = service.set_commitment_status(commitment_id, status)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown commitment")
    return {"id": row.id, "status": row.status}


@router.get("/v1/timeline", dependencies=[Depends(require_token)])
def get_timeline(day: str | None = Query(None, description="YYYY-MM-DD; defaults to today")):
    from datetime import date as _date

    parsed = None
    if day:
        try:
            parsed = _date.fromisoformat(day)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD") from exc
    items = service.timeline(parsed)
    return {"day": (parsed or datetime.now().date()).isoformat(), "count": len(items), "items": items}


@router.get("/v1/search", dependencies=[Depends(require_token)])
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=100),
    include_transcripts: bool = Query(True),
):
    result = service.semantic_search(q, limit=limit)
    if not include_transcripts:
        result.pop("transcripts", None)
    return result


@router.get("/v1/health", dependencies=[Depends(require_token)])
def health():
    info = service.stats()
    info["ok"] = True
    info["version"] = __version__
    return info


# -------------------------------------------------------------- recordings


@router.get("/v1/recordings", dependencies=[Depends(require_token)])
def list_recordings(
    limit: int = Query(50, ge=1, le=500),
    status: str | None = Query(None),
    since: str | None = Query(None),
):
    rows = service.list_recordings(limit=limit, status=status, since=_parse_dt(since))
    return {
        "count": len(rows),
        "recordings": [
            {
                "id": r.id,
                "client_id": r.client_id,
                "device": r.device,
                "session_hint": r.session_hint,
                "recorded_at": r.recorded_at.isoformat(),
                "received_at": r.received_at.isoformat(),
                "duration_ms": r.duration_ms,
                "size_bytes": r.size_bytes,
                "status": r.status,
                "error": r.error,
                "language": r.language,
                "chunk_index": r.chunk_index,
                "chunk_total": r.chunk_total,
            }
            for r in rows
        ],
    }


@router.get("/v1/recordings/{recording_id}", dependencies=[Depends(require_token)])
def get_recording(recording_id: str, include_segments: bool = Query(True)):
    with Session(get_engine()) as session:
        rec = session.get(models.Recording, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown recording")
        payload = {
            "id": rec.id,
            "client_id": rec.client_id,
            "device": rec.device,
            "session_hint": rec.session_hint,
            "recorded_at": rec.recorded_at.isoformat(),
            "duration_ms": rec.duration_ms,
            "size_bytes": rec.size_bytes,
            "status": rec.status,
            "error": rec.error,
            "language": rec.language,
            "speech_ms": rec.speech_ms,
        }
        episodes = session.exec(
            select(models.Episode).where(models.Episode.recording_id == recording_id)
        ).all()
        payload["episodes"] = [
            {
                "id": e.id,
                "title": e.title,
                "summary": e.summary,
                "topics": json.loads(e.topics or "[]"),
                "causes": e.cause_count,
                "commitments": e.commitment_count,
            }
            for e in episodes
        ]
        if include_segments:
            segs = session.exec(
                select(models.Segment)
                .where(models.Segment.recording_id == recording_id)
                .order_by(models.Segment.idx)
            ).all()
            payload["segments"] = [
                {
                    "idx": s.idx,
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "speaker": s.speaker,
                    "confidence": s.speaker_confidence,
                    "text": s.text,
                }
                for s in segs
            ]
        return payload


@router.delete("/v1/recordings/{recording_id}", dependencies=[Depends(require_token)])
def delete_recording(recording_id: str, keep_memory: bool = Query(True)):
    """Remove a recording. Long-term memory is retained unless ``keep_memory=false``."""
    with Session(get_engine()) as session:
        rec = session.get(models.Recording, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown recording")
        for model in (models.Segment, models.Commitment, models.Entity, models.Episode):
            for row in session.exec(
                select(model).where(model.recording_id == recording_id)
            ).all():
                session.delete(row)
        for job in session.exec(
            select(models.Job).where(models.Job.recording_id == recording_id)
        ).all():
            session.delete(job)
        for path in (rec.raw_path, rec.audio_path):
            if path:
                Path(path).unlink(missing_ok=True)
        session.delete(rec)
        session.commit()
    return {"deleted": recording_id, "memory_kept": keep_memory}


@router.post("/v1/recordings/{recording_id}/reprocess", dependencies=[Depends(require_token)])
def reprocess(
    recording_id: str,
    index_memory: bool = Query(False, description="also re-write causal edges (may duplicate)"),
):
    with Session(get_engine()) as session:
        rec = session.get(models.Recording, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown recording")
        if not rec.raw_path or not Path(rec.raw_path).exists():
            raise HTTPException(status_code=409, detail="raw audio no longer on disk")
        rec.status = "queued"
        rec.error = None
        session.add(rec)
        job = _enqueue(session, rec.id, stage="pipeline")
        if index_memory:
            job.payload = json.dumps({"index_memory": True})
            session.add(job)
        session.commit()
        return {"recording_id": rec.id, "job_id": job.id, "status": "queued"}


@router.get("/v1/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str):
    with Session(get_engine()) as session:
        job = session.get(models.Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return {
            "id": job.id,
            "recording_id": job.recording_id,
            "stage": job.stage,
            "status": job.status,
            "attempts": job.attempts,
            "error": job.error,
            "result": json.loads(job.result) if job.result else None,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }


# -------------------------------------------------------------- voiceprint


@router.post("/v1/speakers/enroll", dependencies=[Depends(require_token)])
async def enroll_speaker(
    file: UploadFile = File(..., description="10-60s of the owner speaking alone"),
    label: str = Form("主人"),
):
    """Enroll the owner's voiceprint — the physical key to 原则二."""
    from .pipeline.audio import normalize
    from .pipeline.diarize import SpeakerEmbedder

    tmp_raw = settings.raw_dir / f".enroll-{uuid.uuid4().hex}"
    await _stream_to(tmp_raw, _iter_upload(file))
    tmp_wav = settings.audio_dir / f".enroll-{uuid.uuid4().hex}.wav"
    try:
        info = normalize(tmp_raw, tmp_wav)
        embedder = SpeakerEmbedder(settings)
        if not embedder.available:
            raise HTTPException(
                status_code=503,
                detail=(
                    "speaker embedding model unavailable. Install sherpa-onnx and place a "
                    "speaker-embedding .onnx under SS_SPEAKER_MODEL_DIR."
                ),
            )
        vec = embedder.embed_wav(tmp_wav)
        if vec is None:
            raise HTTPException(status_code=422, detail="could not extract a voiceprint (audio too short?)")

        with Session(get_engine()) as session:
            for old in session.exec(
                select(models.Speaker).where(models.Speaker.is_owner == True)  # noqa: E712
            ).all():
                session.delete(old)
            row = models.Speaker(
                label=label,
                is_owner=True,
                embedding=vec.tobytes(),
                dim=int(vec.shape[0]),
                model=embedder._model_name,
                sample_seconds=info.duration_ms / 1000.0,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
        return {
            "speaker_id": row.id,
            "label": row.label,
            "dim": row.dim,
            "sample_seconds": round(row.sample_seconds, 1),
        }
    finally:
        tmp_raw.unlink(missing_ok=True)
        tmp_wav.unlink(missing_ok=True)


@router.get("/v1/speakers", dependencies=[Depends(require_token)])
def list_speakers():
    with Session(get_engine()) as session:
        rows = session.exec(select(models.Speaker)).all()
        return {
            "count": len(rows),
            "speakers": [
                {
                    "id": r.id,
                    "label": r.label,
                    "is_owner": r.is_owner,
                    "dim": r.dim,
                    "model": r.model,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ],
        }


def create_app():
    """Build the FastAPI application."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    settings.ensure_dirs()
    init_db()

    app = FastAPI(
        title="ShadowScribe",
        version=__version__,
        description=(
            "影书 — 无感因果外脑。Ingest ambient audio, distil it into causal memory, "
            "and hand a desktop agent the context it would otherwise have to be told."
        ),
        docs_url="/docs",
        redoc_url=None,
    )
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "service": "shadowscribe", "version": __version__}

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "service": "ShadowScribe 影书",
            "version": __version__,
            "tagline": "让 AI 记住因果，而不是让你重复背景",
            "docs": "/docs",
            "ingest": "/v1/ingest/audio",
            "brief": "/v1/context/brief",
            "memory_backend": get_backend(settings).name,
            "dev_token": settings.effective_token if settings.dev_token_generated else None,
        }

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # pragma: no cover
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    return app
