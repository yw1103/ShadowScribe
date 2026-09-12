"""Pipeline orchestration: audio blob → causal memory.

One function owns the whole chain so the worker, the API and the test-suite all
exercise the same code path:

    normalise → transcribe → attribute speakers → distil → persist → remember
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlmodel import Session, delete, desc, select

from .. import models
from ..config import settings as default_settings
from ..db import get_engine
from ..memory import get_backend
from . import text
from .asr import Transcriber
from .audio import normalize
from .diarize import SpeakerEmbedder, SpeakerLabeler
from .distill import DistillationResult, Distiller

log = logging.getLogger(__name__)


class Pipeline:
    """Stateful worker-side pipeline. Model-heavy, so instantiate once per process."""

    def __init__(self, settings=None, backend=None) -> None:
        self.s = settings or default_settings
        self.backend = backend or get_backend(self.s)
        self.transcriber = Transcriber(self.s)
        self.embedder = SpeakerEmbedder(self.s)
        self.labeler = SpeakerLabeler(self.s, self.embedder)
        self.distiller = Distiller(self.s)
        self._owner_key: tuple[str, object] | None = None
        """Identity of the voiceprint currently loaded, so a re-enrolment is picked
        up without restarting the worker."""

    # ------------------------------------------------------------- owner voice
    def _load_owner(self, session: Session) -> None:
        """Adopt the enrolled voiceprint, re-reading it whenever it changes.

        Deliberately *not* a load-once-per-process cache. Enrolling a voiceprint
        over the API has to take effect on the next job, not after a worker
        restart — caught live when enrolling the owner's own voice and
        reprocessing produced byte-identical similarity scores, meaning the worker
        was still comparing against the previous voiceprint.
        """
        if self.s.diarization != "embedding":
            return

        row = session.exec(
            select(models.Speaker)
            .where(models.Speaker.is_owner == True)  # noqa: E712
            .order_by(desc(models.Speaker.created_at))
            .limit(1)
        ).first()

        if row is None or not row.embedding:
            if self._owner_key is not None:
                log.info("owner voiceprint removed; speaker labels will stay 'unknown'")
            self._owner_key = None
            self.labeler.load_owner(None)
            return

        key = (row.id, row.created_at)
        if key == self._owner_key:
            return

        import numpy as np

        self.labeler.load_owner(np.frombuffer(row.embedding, dtype=np.float32), row.label)
        self._owner_key = key
        log.info("owner voiceprint loaded: %s (dim=%d)", row.label, row.dim)

    def reload_owner(self) -> None:
        """Force the next job to re-read the voiceprint from the database."""
        self._owner_key = None

    # ------------------------------------------------------------------- main
    def process(self, recording_id: str, *, index_memory: bool = True) -> dict:
        engine = get_engine(self.s.db_path)
        with Session(engine) as session:
            rec = session.get(models.Recording, recording_id)
            if rec is None:
                raise ValueError(f"recording {recording_id} not found")

            rec.status = "processing"
            rec.error = None
            session.add(rec)
            session.commit()
            session.refresh(rec)

            try:
                outcome = self._run(session, rec, index_memory=index_memory)
            except Exception as exc:
                log.exception("pipeline failed for %s", recording_id)
                rec.status = "failed"
                rec.error = f"{type(exc).__name__}: {exc}"[:2000]
                session.add(rec)
                session.commit()
                raise

            rec.status = "done"
            rec.processed_at = datetime.now(timezone.utc)
            rec.error = None
            session.add(rec)
            session.commit()
            return outcome

    # ----------------------------------------------------------------- stages
    def _run(self, session: Session, rec: models.Recording, *, index_memory: bool) -> dict:
        self._load_owner(session)

        # 1. normalise -------------------------------------------------------
        wav = self._wav_path(rec)
        if not wav.exists():
            source = Path(rec.raw_path) if rec.raw_path else None
            if source is None or not source.exists():
                raise FileNotFoundError(f"no source audio for recording {rec.id}")
            log.info("[%s] normalising %s", rec.id, source.name)
            info = normalize(source, wav)
            rec.audio_path = str(wav)
            if not rec.duration_ms:
                rec.duration_ms = info.duration_ms
        rec.audio_path = str(wav)
        session.add(rec)
        session.commit()

        # 2. transcribe ------------------------------------------------------
        segments, language = self.transcriber.transcribe(wav)
        # Normalise orthography once, here, so segments / facts / edges / the
        # context card all agree. Doing it later would leave raw segments
        # searchable only in whichever script Whisper happened to pick.
        if language and language.startswith("zh"):
            text.normalize_segments(segments, enabled=self.s.simplify_chinese)
        rec.language = language
        rec.speech_ms = segments[-1].end_ms if segments else 0
        if not rec.duration_ms:
            rec.duration_ms = rec.speech_ms
        session.add(rec)
        session.commit()

        if not segments:
            log.info("[%s] no speech detected", rec.id)
            self._clear_derived(session, rec.id)
            self._maybe_drop_audio(rec)
            return {"recording_id": rec.id, "segments": 0, "episodes": 0, "speech": False}

        # 3. speaker attribution --------------------------------------------
        matches = self.labeler.label(wav, segments)
        labels = [m.label for m in matches]

        self._clear_derived(session, rec.id)
        for i, (seg, match) in enumerate(zip(segments, matches, strict=True)):
            session.add(
                models.Segment(
                    recording_id=rec.id,
                    idx=i,
                    start_ms=seg.start_ms,
                    end_ms=seg.end_ms,
                    text=seg.text,
                    speaker=match.label,
                    speaker_confidence=match.confidence,
                    avg_logprob=seg.avg_logprob,
                    no_speech_prob=seg.no_speech_prob,
                )
            )
        session.commit()

        # 4. distil ----------------------------------------------------------
        recorded_at = self._local_date(rec)
        results = self.distiller.distill_long(
            segments,
            labels=None if self.s.diarization == "off" else labels,
            recorded_at=recorded_at,
            session_hint=rec.session_hint,
        )

        # 5. persist ---------------------------------------------------------
        written = {"edges": 0, "facts": 0, "commitments": 0, "episodes": 0}
        for i, result in enumerate(results):
            window = self._window_for(segments, results, i)
            episode = self._persist_episode(session, rec, result, i, window, labels, recorded_at)
            written["episodes"] += 1
            written["commitments"] += len(result.commitments)
            if index_memory:
                ep_input = result.to_episode_input(
                    occurred_at=recorded_at,
                    session_context=rec.session_hint,
                )
                outcome = self.backend.write_episode(ep_input)
                written["edges"] += outcome.edges_written
                written["facts"] += outcome.facts_written
                episode.memory_ids = json.dumps(outcome.ids)
                session.add(episode)
                if outcome.errors:
                    log.warning(
                        "[%s] memory backend reported %d errors", rec.id, len(outcome.errors)
                    )
        session.commit()

        self._maybe_drop_audio(rec)
        log.info(
            "[%s] done: %d segments, %d episodes, %d causal edges, %d commitments",
            rec.id,
            len(segments),
            written["episodes"],
            written["edges"],
            written["commitments"],
        )
        return {
            "recording_id": rec.id,
            "segments": len(segments),
            "speech": True,
            "language": language,
            **written,
        }

    # ---------------------------------------------------------------- helpers
    def _wav_path(self, rec: models.Recording) -> Path:
        return self.s.audio_dir / f"{rec.id}.wav"

    def _local_date(self, rec: models.Recording) -> str:
        """Conversation date in the operator's timezone — drives 今天/周四 resolution."""
        try:
            tz = ZoneInfo(self.s.timezone)
        except Exception:
            log.warning("timezone %r unavailable; using UTC dates", self.s.timezone)
            tz = timezone.utc
        dt = rec.recorded_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).date().isoformat()

    @staticmethod
    def _window_for(segments, results, index) -> list:
        """Best-effort recovery of which segments fed window ``index``."""
        if not results:
            return []
        per = max(1, len(segments) // len(results))
        return segments[index * per : (index + 1) * per]

    def _clear_derived(self, session: Session, recording_id: str) -> None:
        """Make reprocessing idempotent by dropping previously derived rows."""
        for model in (models.Segment, models.Commitment, models.Entity):
            session.exec(delete(model).where(model.recording_id == recording_id))
        session.exec(delete(models.Episode).where(models.Episode.recording_id == recording_id))
        session.commit()

    def _persist_episode(
        self,
        session: Session,
        rec: models.Recording,
        result: DistillationResult,
        index: int,
        window: list,
        labels: list[str],
        recorded_at: str,
    ) -> models.Episode:
        started = rec.recorded_at
        ended = None
        if window:
            ended = started + timedelta(milliseconds=window[-1].end_ms)

        episode = models.Episode(
            recording_id=rec.id,
            window_index=index,
            title=result.title or f"{rec.id[:8]} #{index + 1}",
            summary=result.summary,
            topics=json.dumps(result.topics, ensure_ascii=False),
            started_at=started,
            ended_at=ended,
            speakers=json.dumps(sorted({s for s in labels if s}), ensure_ascii=False),
            cause_count=len(result.edges),
            commitment_count=len(result.commitments),
            raw_json=json.dumps(
                {
                    "edges": [
                        {
                            "cause": e.cause,
                            "effect": e.effect,
                            "relation": e.normalized_relation(),
                            "actor": e.actor,
                            "task_tag": e.task_tag,
                            "evidence": e.evidence,
                            "confidence": e.confidence,
                        }
                        for e in result.edges
                    ],
                    "decisions": [
                        {"what": d.what, "rationale": d.rationale, "actor": d.actor}
                        for d in result.decisions
                    ],
                    "degraded": result.degraded,
                },
                ensure_ascii=False,
            ),
        )
        session.add(episode)
        session.flush()

        for c in result.commitments:
            session.add(
                models.Commitment(
                    episode_id=episode.id,
                    recording_id=rec.id,
                    what=c.what,
                    owner=c.owner,
                    counterparty=c.counterparty,
                    due_text=c.due_text,
                    due_at=_parse_due(c.due_date),
                    confidence=c.confidence,
                    evidence=c.evidence,
                )
            )

        for ent in result.entities:
            session.add(
                models.Entity(
                    name=ent["name"],
                    kind=ent.get("kind") or "other",
                    episode_id=episode.id,
                    recording_id=rec.id,
                    aliases=json.dumps(ent.get("aliases") or [], ensure_ascii=False),
                )
            )

        session.add(episode)
        return episode

    def _maybe_drop_audio(self, rec: models.Recording) -> None:
        if self.s.keep_audio:
            return
        for path in (rec.raw_path, rec.audio_path):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError as exc:
                    log.debug("could not remove %s: %s", path, exc)

    def prune_audio(self, older_than_days: int | None = None) -> int:
        """Delete archived audio older than the retention window. Returns files removed."""
        days = older_than_days if older_than_days is not None else self.s.audio_retention_days
        if days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        removed = 0
        with Session(get_engine(self.s.db_path)) as session:
            for rec in session.exec(select(models.Recording)).all():
                for attr in ("raw_path", "audio_path"):
                    path = getattr(rec, attr)
                    if not path:
                        continue
                    p = Path(path)
                    try:
                        if p.exists() and p.stat().st_mtime < cutoff:
                            p.unlink()
                            removed += 1
                    except OSError:
                        continue
                if rec.status == "done":
                    rec.raw_path = None
        return removed

    def disk_usage(self) -> dict:
        def _size(path: Path) -> int:
            if not path.exists():
                return 0
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

        total, used, free = shutil.disk_usage(self.s.data_dir)
        return {
            "data_bytes": _size(self.s.data_dir),
            "audio_bytes": _size(self.s.audio_dir),
            "raw_bytes": _size(self.s.raw_dir),
            "disk_total": total,
            "disk_free": free,
        }


def _parse_due(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
