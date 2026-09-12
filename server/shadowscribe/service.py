"""Query service — the read side that makes the whole thing worth running.

``build_brief`` is the product. Everything else (upload, ASR, distillation) exists
so that one function can hand a desktop agent a compact, faithful picture of what
happened in the physical world today, with zero effort from the user.

Output is deliberately plain markdown: it must paste cleanly into Cursor, Claude
Desktop, a terminal, or an MCP tool result without any further transformation.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, desc, select

from . import models
from .config import settings as default_settings
from .db import get_engine
from .memory import get_backend

log = logging.getLogger(__name__)

#: Rough token cost of one CJK char. Only used for budget trimming.
_CHARS_PER_TOKEN = 1.6


@dataclass(slots=True)
class BriefOptions:
    hours: int = 24
    since: datetime | None = None
    until: datetime | None = None
    max_tokens: int = 1800
    include_quotes: bool = True
    include_entities: bool = True


def _tz(settings):
    """Resolve the operator timezone, degrading to UTC when tzdata is absent.

    ``zoneinfo`` needs the system tz database (``tzdata`` package on Windows and
    on slim container images). Missing it must not take down ``/v1/context/brief``.
    """
    try:
        return ZoneInfo(settings.timezone)
    except Exception as exc:
        log.warning(
            "timezone %r unavailable (%s); falling back to UTC. "
            "Install the `tzdata` package for correct local-day attribution.",
            settings.timezone,
            exc,
        )
        return timezone.utc


def _aware(dt: datetime | None, tz) -> datetime | None:
    if dt is None:
        return None
    return (
        dt.replace(tzinfo=timezone.utc).astimezone(tz) if dt.tzinfo is None else dt.astimezone(tz)
    )


def _approx_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN) + 1


# --------------------------------------------------------------------- brief


def build_brief(options: BriefOptions | None = None, settings=None) -> str:
    """Render the context card. Returns markdown."""
    opts = options or BriefOptions()
    s = settings or default_settings
    tz = _tz(s)

    until = opts.until or datetime.now(timezone.utc)
    since = opts.since or (until - timedelta(hours=opts.hours))
    since_naive = since.astimezone(timezone.utc).replace(tzinfo=None)
    until_naive = until.astimezone(timezone.utc).replace(tzinfo=None)

    engine = get_engine(s.db_path)
    with Session(engine) as session:
        recordings = session.exec(
            select(models.Recording)
            .where(
                models.Recording.recorded_at >= since_naive,
                models.Recording.recorded_at <= until_naive,
                models.Recording.status == "done",
            )
            .order_by(models.Recording.recorded_at)
        ).all()
        rec_ids = [r.id for r in recordings]

        episodes = (
            session.exec(
                select(models.Episode)
                .where(models.Episode.recording_id.in_(rec_ids))
                .order_by(models.Episode.started_at)
            ).all()
            if rec_ids
            else []
        )
        commitments = (
            session.exec(
                select(models.Commitment)
                .where(models.Commitment.recording_id.in_(rec_ids))
                .order_by(models.Commitment.created_at)
            ).all()
            if rec_ids
            else []
        )
        entities = (
            session.exec(select(models.Entity).where(models.Entity.recording_id.in_(rec_ids))).all()
            if rec_ids
            else []
        )
        segments = (
            session.exec(
                select(models.Segment)
                .where(models.Segment.recording_id.in_(rec_ids))
                .order_by(models.Segment.recording_id, models.Segment.start_ms)
            ).all()
            if rec_ids
            else []
        )

    local_since = _aware(since_naive, tz)
    local_until = _aware(until_naive, tz)
    span = f"{local_since:%Y-%m-%d %H:%M} → {local_until:%m-%d %H:%M}"

    if not recordings:
        return _empty_card(span, since_naive, s)

    open_commitments = [c for c in commitments if c.status == "open"]
    entity_names = _top_entities(entities)

    overview = (
        f"# 影书 · 现实上下文（{span}）\n\n"
        f"**概览**：{len(recordings)} 段录音 · {len(episodes)} 个话题片段 · "
        f"{len(open_commitments)} 项待办 · {sum(e.cause_count for e in episodes)} 条因果。"
    )

    # Sections are ordered by value to the reader, and the assembler below relies
    # on that order: commitments are *mandatory* (only their items get trimmed),
    # everything after them is optional and dropped whole when the budget runs out.
    mandatory = ("⏳ 进行中的承诺", [_render_commitment(c, tz) for c in open_commitments])
    optional: list[tuple[str, list[str]]] = [
        ("🎯 关键决策", _render_decisions(episodes)),
        ("🔗 因果脉络", _render_edges(episodes)),
        ("🗂 话题片段", _render_episode_frame(episodes, tz)),
    ]
    if opts.include_entities and entity_names:
        optional.append(("👥 涉及的人与项目", [" · ".join(entity_names)]))
    if opts.include_quotes:
        optional.append(("💬 原话锚点", _best_quotes(segments, limit=6)))

    return _assemble(overview, mandatory, optional, opts.max_tokens)


FOOTER = (
    "\n\n---\n"
    "_由影书 ShadowScribe 从现实对话静默沉淀。下列内容为客观转录与结构化抽取，"
    "可直接作为工作背景使用。_\n"
)


def _block(title: str, lines: list[str]) -> str:
    return "\n".join([f"## {title}", *lines])


def _assemble(
    overview: str,
    mandatory: tuple[str, list[str]],
    optional: list[tuple[str, list[str]]],
    max_tokens: int,
) -> str:
    """Fit sections into the token budget without ever cutting mid-sentence.

    Two rules make the result predictable:

    * the mandatory section is always present — if it alone busts the budget we
      shorten its *list* and say how many entries were withheld, because "what do
      I owe people" is the one thing the card must never lose;
    * optional sections are dropped whole, and a section that does not fit does
      not stop a later, smaller one from fitting.
    """
    parts = [overview]
    used = _approx_tokens(overview)
    trimmed = False

    title, lines = mandatory
    if lines:
        kept: list[str] = []
        for line in lines:
            if kept and used + _approx_tokens(_block(title, [*kept, line])) > max_tokens:
                trimmed = True
                break
            kept.append(line)
        if len(kept) < len(lines):
            kept.append(f"- _…另有 {len(lines) - len(kept)} 项未列出_")
        block = _block(title, kept)
        parts.append(block)
        used += _approx_tokens(block)

    for title, lines in optional:
        if not lines:
            continue
        block = _block(title, lines)
        if used + _approx_tokens(block) > max_tokens:
            trimmed = True
            continue
        parts.append(block)
        used += _approx_tokens(block)

    body = "\n\n".join(parts)
    if trimmed:
        body += "\n\n_（已按 token 预算截断，可提高 max_tokens 获取完整上下文）_"
    return body + FOOTER


def _render_decisions(episodes: list[models.Episode]) -> list[str]:
    out: list[str] = []
    for d in _collect_decisions(episodes):
        who = {"owner": "我拍的", "other": "对方拍的", "unknown": ""}.get(d.get("actor", ""), "")
        suffix = f"（{who}）" if who else ""
        rationale = f" — 依据：{d['rationale']}" if d.get("rationale") else ""
        out.append(f"- {d['what']}{suffix}{rationale}")
    return out


_EDGE_GLYPH = {
    "caused": "→",
    "enabled": "→(促成)",
    "prevented": "→(避免)",
    "no_effect": "→(无影响)",
}


def _render_edges(episodes: list[models.Episode]) -> list[str]:
    out: list[str] = []
    for e in _collect_edges(episodes):
        tag = f"`{e['task_tag']}`" if e.get("task_tag") else ""
        glyph = _EDGE_GLYPH.get(e.get("relation", "caused"), "→")
        out.append(f"- {e['cause']} {glyph} {e['effect']} {tag}".rstrip())
    return out


def _empty_card(span: str, since_naive: datetime, s) -> str:
    """Say *why* the window is empty.

    "No recordings" and "no recordings *inside this window*" look identical to the
    reader otherwise, and the second one is a silent failure: the pipeline worked,
    the user sees nothing, and there is no hint about what to change.
    """
    with Session(get_engine(s.db_path)) as session:
        latest = session.exec(
            select(models.Recording)
            .where(models.Recording.status == "done")
            .order_by(desc(models.Recording.recorded_at))
            .limit(1)
        ).first()
        pending = session.exec(
            select(models.Recording)
            .where(models.Recording.status.in_(["queued", "processing"]))
            .order_by(desc(models.Recording.received_at))
            .limit(1)
        ).first()

    body = ["# 影书 · 现实上下文", "", f"_统计区间 {span}：没有已处理的录音。_"]
    if pending is not None:
        body.append(
            f"> ⏳ 有录音正在处理中（最近接收于 {pending.received_at:%Y-%m-%d %H:%M}），稍后重跑即可。"
        )
    if latest is not None and latest.recorded_at < since_naive:
        hours = int(
            (datetime.now(timezone.utc).replace(tzinfo=None) - latest.recorded_at).total_seconds()
            // 3600
        )
        body.append(
            f"> 📼 更早还有 {latest.recorded_at:%Y-%m-%d} 的录音，不在当前窗口内。"
            f"用 `ss brief --hours {hours + 1}` 取回。"
        )
    if pending is None and latest is None:
        body.append("> 提示：手机端上传音频后，运行 `ss status` 查看处理进度。")
    body.append("")
    return "\n".join(body)


def _render_episode_frame(episodes: list[models.Episode], tz, limit: int = 10) -> list[str]:
    """Chronological frame of what was talked about.

    This is the fallback that keeps the card useful when distillation ran
    degraded (no LLM key, or every window failed): there are no edges to show,
    but the titles and summaries still tell the agent where the day went.
    """
    if not episodes:
        return []
    lines: list[str] = []
    for ep in episodes[:limit]:
        stamp = _aware(ep.started_at, tz)
        clock = f"{stamp:%H:%M}" if stamp else "--:--"
        title = (ep.title or "(未命名)").strip()
        degraded = False
        with contextlib.suppress(json.JSONDecodeError):
            degraded = bool(json.loads(ep.raw_json or "{}").get("degraded"))
        mark = " _(未蒸馏)_" if degraded else ""
        summary = " ".join((ep.summary or "").split())
        if summary and summary != title:
            lines.append(f"- **{clock} {title}**{mark} — {summary[:110]}")
        else:
            lines.append(f"- **{clock} {title}**{mark}")
    if len(episodes) > limit:
        lines.append(f"- _…另有 {len(episodes) - limit} 个片段_")
    return lines


def _render_commitment(c: models.Commitment, tz) -> str:
    due = ""
    if c.due_at:
        due = f" · 截止 {_aware(c.due_at, tz):%m-%d}"
    elif c.due_text:
        due = f" · 截止 {c.due_text}"
    to = f" → {c.counterparty}" if c.counterparty else ""
    owner = "" if c.owner in ("我", "owner", "") else f"[{c.owner}] "
    conf = f" _(置信 {c.confidence:.0%})_" if c.confidence and c.confidence < 0.6 else ""
    return f"- [ ] {owner}{c.what}{to}{due}{conf}"


def _collect_decisions(episodes: list[models.Episode]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for ep in episodes:
        try:
            payload = json.loads(ep.raw_json or "{}")
        except json.JSONDecodeError:
            continue
        for d in payload.get("decisions", []):
            what = (d.get("what") or "").strip()
            if what and what not in seen:
                seen.add(what)
                out.append(d)
    return out[:12]


def _collect_edges(episodes: list[models.Episode]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ep in episodes:
        try:
            payload = json.loads(ep.raw_json or "{}")
        except json.JSONDecodeError:
            continue
        for e in payload.get("edges", []):
            key = (e.get("cause", ""), e.get("effect", ""))
            if key[0] and key[1] and key not in seen:
                seen.add(key)
                out.append(e)
    return out[:15]


def _top_entities(entities: list[models.Entity], limit: int = 12) -> list[str]:
    counts: dict[str, tuple[int, str]] = {}
    for e in entities:
        n, kind = counts.get(e.name, (0, e.kind))
        counts[e.name] = (n + e.mentions, kind)
    ordered = sorted(counts.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    glyph = {"person": "👤", "org": "🏢", "project": "📦", "artifact": "🧩", "place": "📍"}
    return [f"{glyph.get(kind, '•')}{name}" for name, (_, kind) in ordered]


def _best_quotes(segments: list[models.Segment], limit: int = 6) -> list[str]:
    """Prefer the owner's own words, longest first — they carry intent best."""
    ranked = sorted(
        (s for s in segments if len(s.text) >= 8 and (s.avg_logprob or 0) > -1.0),
        key=lambda s: (s.speaker == "owner", len(s.text)),
        reverse=True,
    )
    out: list[str] = []
    for seg in ranked[:limit]:
        who = {"owner": "我", "guest": "对方"}.get(seg.speaker or "", "未知")
        out.append(
            f"[{seg.start_ms // 60000:02d}:{(seg.start_ms // 1000) % 60:02d}] {who}: “{seg.text}”"
        )
    return out


# ------------------------------------------------------------------ queries


def list_recordings(
    *, limit: int = 50, since: datetime | None = None, status: str | None = None, settings=None
) -> list[models.Recording]:
    s = settings or default_settings
    with Session(get_engine(s.db_path)) as session:
        stmt = select(models.Recording).order_by(desc(models.Recording.recorded_at)).limit(limit)
        if since is not None:
            stmt = stmt.where(models.Recording.recorded_at >= since)
        if status:
            stmt = stmt.where(models.Recording.status == status)
        return list(session.exec(stmt).all())


def list_commitments(
    *, status: str = "open", limit: int = 50, since: datetime | None = None, settings=None
) -> list[models.Commitment]:
    s = settings or default_settings
    with Session(get_engine(s.db_path)) as session:
        stmt = select(models.Commitment).order_by(desc(models.Commitment.created_at)).limit(limit)
        if status != "all":
            stmt = stmt.where(models.Commitment.status == status)
        if since is not None:
            stmt = stmt.where(models.Commitment.created_at >= since)
        return list(session.exec(stmt).all())


def set_commitment_status(
    commitment_id: str, status: str, settings=None
) -> models.Commitment | None:
    s = settings or default_settings
    with Session(get_engine(s.db_path)) as session:
        row = session.get(models.Commitment, commitment_id)
        if row is None:
            return None
        row.status = status
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def timeline(day: date | None = None, settings=None) -> list[dict]:
    """Every episode on a calendar day, in order."""
    s = settings or default_settings
    tz = _tz(s)
    target = day or datetime.now(tz).date()
    start = datetime.combine(target, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = start + timedelta(days=1)

    with Session(get_engine(s.db_path)) as session:
        episodes = session.exec(
            select(models.Episode)
            .where(
                models.Episode.started_at >= start.replace(tzinfo=None),
                models.Episode.started_at < end.replace(tzinfo=None),
            )
            .order_by(models.Episode.started_at)
        ).all()
        out: list[dict] = []
        for ep in episodes:
            rec = session.get(models.Recording, ep.recording_id)
            out.append(
                {
                    "id": ep.id,
                    "recording_id": ep.recording_id,
                    "time": _aware(ep.started_at, tz).strftime("%H:%M"),
                    "title": ep.title,
                    "summary": ep.summary,
                    "topics": json.loads(ep.topics or "[]"),
                    "causes": ep.cause_count,
                    "commitments": ep.commitment_count,
                    "session_hint": rec.session_hint if rec else None,
                    "degraded": json.loads(ep.raw_json or "{}").get("degraded", False),
                }
            )
        return out


def semantic_search(query: str, *, limit: int = 10, settings=None) -> dict:
    """Memory-backend search, plus a raw transcript sweep so nothing is invisible."""
    s = settings or default_settings
    backend = get_backend(s)
    memory_hits = ""
    try:
        memory_hits = backend.search(query, limit=limit, detail_level="l1")
    except Exception as exc:
        log.warning("memory search failed: %s", exc)
        memory_hits = f"(memory backend error: {exc})"

    with Session(get_engine(s.db_path)) as session:
        rows = session.exec(
            select(models.Segment)
            .where(models.Segment.text.contains(query))
            .order_by(desc(models.Segment.id))
            .limit(limit)
        ).all()
        transcripts = [
            {
                "recording_id": r.recording_id,
                "start_ms": r.start_ms,
                "speaker": r.speaker,
                "text": r.text,
            }
            for r in rows
        ]
    return {"query": query, "memory": memory_hits, "transcripts": transcripts}


def stats(settings=None) -> dict:
    s = settings or default_settings
    with Session(get_engine(s.db_path)) as session:
        counts = {
            "recordings": len(session.exec(select(models.Recording.id)).all()),
            "done": len(
                session.exec(
                    select(models.Recording.id).where(models.Recording.status == "done")
                ).all()
            ),
            "queued": len(
                session.exec(
                    select(models.Recording.id).where(models.Recording.status == "queued")
                ).all()
            ),
            "failed": len(
                session.exec(
                    select(models.Recording.id).where(models.Recording.status == "failed")
                ).all()
            ),
            "segments": len(session.exec(select(models.Segment.id)).all()),
            "episodes": len(session.exec(select(models.Episode.id)).all()),
            "commitments_open": len(
                session.exec(
                    select(models.Commitment.id).where(models.Commitment.status == "open")
                ).all()
            ),
        }
    backend = get_backend(s)
    try:
        memory = backend.stats()
    except Exception as exc:  # a broken store must not take down /v1/health
        memory = {"error": f"{type(exc).__name__}: {exc}"}
    return {"recordings": counts, "memory": memory, "backend": backend.name}
