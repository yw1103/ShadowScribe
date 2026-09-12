"""HTTP surface tests.

These deliberately never touch Whisper or an LLM: they assert the *contract* the
phone client depends on (auth, idempotency, status codes, resumable uploads) —
which is exactly the part that must not silently drift.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tests.conftest import AUTH, SILENT_WAV

# ------------------------------------------------------------------ liveness


def test_healthz_needs_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "shadowscribe"


def test_root_advertises_the_entry_points(client):
    body = client.get("/").json()
    assert body["ingest"] == "/v1/ingest/audio"
    assert body["brief"] == "/v1/context/brief"
    # dev mode must not leak a token through the unauthenticated root
    assert body["dev_token"] is None


def test_v1_routes_reject_missing_token(client):
    assert client.get("/v1/health").status_code == 401
    assert client.get("/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_v1_health_with_token(client):
    body = client.get("/v1/health", headers=AUTH).json()
    assert body["ok"] is True
    assert body["version"]


# -------------------------------------------------------------------- ingest


def test_ingest_audio_queues_a_job(client):
    resp = client.post(
        "/v1/ingest/audio",
        headers=AUTH,
        files={"file": ("memo.wav", SILENT_WAV, "audio/wav")},
        data={"client_id": "pixel-8", "session_hint": "与老王在会议室"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["dedup"] is False
    assert body["job_id"]

    # the recording is visible immediately, before the worker touches it
    listing = client.get("/v1/recordings", headers=AUTH).json()
    assert listing["count"] == 1
    assert listing["recordings"][0]["session_hint"] == "与老王在会议室"


def test_ingest_is_idempotent_by_content_hash(client):
    """Phone clients retry aggressively; identical bytes must not duplicate memory."""
    first = client.post(
        "/v1/ingest/audio",
        headers=AUTH,
        files={"file": ("a.wav", SILENT_WAV, "audio/wav")},
        data={"client_id": "pixel-8"},
    ).json()
    second = client.post(
        "/v1/ingest/audio",
        headers=AUTH,
        files={"file": ("b.wav", SILENT_WAV, "audio/wav")},
        data={"client_id": "pixel-8"},
    ).json()

    assert second["dedup"] is True
    assert second["recording_id"] == first["recording_id"]
    assert client.get("/v1/recordings", headers=AUTH).json()["count"] == 1


def test_ingest_rejects_oversized_upload(client, monkeypatch):
    from shadowscribe.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 0)
    resp = client.post(
        "/v1/ingest/audio",
        headers=AUTH,
        files={"file": ("big.wav", SILENT_WAV, "audio/wav")},
        data={"client_id": "pixel-8"},
    )
    assert resp.status_code == 413
    # a rejected upload must not leave a half-written recording behind
    assert client.get("/v1/recordings", headers=AUTH).json()["count"] == 0


def test_ingest_raw_body(client):
    resp = client.post(
        "/v1/ingest/raw",
        headers={**AUTH, "Content-Type": "audio/wav"},
        params={"filename": "raw.wav", "client_id": "cli"},
        content=SILENT_WAV,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["bytes"] == len(SILENT_WAV)


def test_ingest_audio_requires_token(client):
    resp = client.post(
        "/v1/ingest/audio",
        files={"file": ("a.wav", SILENT_WAV, "audio/wav")},
    )
    assert resp.status_code == 401


# ----------------------------------------------------------- resumable uploads


def test_chunked_upload_roundtrip(client):
    session = client.post(
        "/v1/uploads",
        headers=AUTH,
        params={"filename": "day.m4a", "total_parts": 3, "client_id": "pixel-8"},
    ).json()
    upload_id = session["upload_id"]

    # two halves, out of order on purpose — assembly must sort numerically
    client.put(f"/v1/uploads/{upload_id}/parts/1", headers=AUTH, content=SILENT_WAV[20:])
    client.put(f"/v1/uploads/{upload_id}/parts/0", headers=AUTH, content=SILENT_WAV[:20])

    resp = client.post(f"/v1/uploads/{upload_id}/complete", headers=AUTH)
    assert resp.status_code == 400  # 3 declared, 2 sent

    client.put(f"/v1/uploads/{upload_id}/parts/2", headers=AUTH, content=SILENT_WAV[20:])
    resp = client.post(f"/v1/uploads/{upload_id}/complete", headers=AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dedup"] is False
    assert body["bytes"] == len(SILENT_WAV) + len(SILENT_WAV) - 20


def test_chunked_upload_part_is_reuploadable(client):
    upload_id = client.post(
        "/v1/uploads", headers=AUTH, params={"filename": "x.m4a", "total_parts": 1}
    ).json()["upload_id"]

    first = client.put(f"/v1/uploads/{upload_id}/parts/0", headers=AUTH, content=b"abc").json()
    second = client.put(f"/v1/uploads/{upload_id}/parts/0", headers=AUTH, content=b"abcdef").json()

    assert first["received_parts"] == 1
    assert second["received_parts"] == 1  # overwritten, not appended
    assert second["bytes"] == 6


def test_complete_unknown_upload(client):
    assert client.post("/v1/uploads/nope/complete", headers=AUTH).status_code == 404


# -------------------------------------------------------------------- recall


def test_brief_on_empty_store_is_a_usable_markdown_card(client):
    resp = client.get("/v1/context/brief", headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "影书" in resp.text
    assert "没有已处理的录音" in resp.text


def test_commitments_empty(client):
    body = client.get("/v1/commitments", headers=AUTH).json()
    assert body == {"status": "open", "count": 0, "commitments": []}


def test_timeline_rejects_bad_day(client):
    assert client.get("/v1/timeline", headers=AUTH, params={"day": "08-01-2026"}).status_code == 400


def test_search_without_memory_backend_content(client):
    body = client.get("/v1/search", headers=AUTH, params={"q": "登录页"}).json()
    assert body["query"] == "登录页"
    assert "memory" in body
    assert body["transcripts"] == []


def test_search_requires_query(client):
    assert client.get("/v1/search", headers=AUTH).status_code == 422


# ---------------------------------------------------------------- recordings


def test_recording_detail_and_delete(client):
    rec_id = client.post(
        "/v1/ingest/audio",
        headers=AUTH,
        files={"file": ("a.wav", SILENT_WAV, "audio/wav")},
        data={"client_id": "pixel-8"},
    ).json()["recording_id"]

    detail = client.get(f"/v1/recordings/{rec_id}", headers=AUTH).json()
    assert detail["id"] == rec_id
    assert detail["segments"] == []

    assert client.delete(f"/v1/recordings/{rec_id}", headers=AUTH).status_code == 200
    assert client.get(f"/v1/recordings/{rec_id}", headers=AUTH).status_code == 404


def test_unknown_recording_is_404(client):
    assert client.get("/v1/recordings/deadbeef", headers=AUTH).status_code == 404
    assert client.get("/v1/jobs/deadbeef", headers=AUTH).status_code == 404


# ------------------------------------------------------------------ speakers


def test_speakers_empty_then_enroll_without_model(client):
    assert client.get("/v1/speakers", headers=AUTH).json()["count"] == 0

    resp = client.post(
        "/v1/speakers/enroll",
        headers=AUTH,
        files={"file": ("me.wav", SILENT_WAV, "audio/wav")},
        data={"label": "主人"},
    )
    # No ffmpeg / no sherpa model in CI: the route must fail loudly and specifically,
    # never silently pretend enrollment worked.
    assert resp.status_code in (422, 503, 500)


# --------------------------------------------------------------------- brief


def test_brief_includes_distilled_content(client, clean_db):
    """The product-level assertion: a distilled episode must reach the card."""
    from sqlmodel import Session

    from shadowscribe import models

    with Session(clean_db) as session:
        rec = models.Recording(
            client_id="pixel-8",
            session_hint="与老王在会议室",
            recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            status="done",
            duration_ms=60_000,
        )
        session.add(rec)
        session.flush()

        episode = models.Episode(
            recording_id=rec.id,
            title="登录页排期对齐",
            summary="老王反馈 Safari 白屏。",
            topics=json.dumps(["登录页"], ensure_ascii=False),
            raw_json=json.dumps(
                {
                    "edges": [
                        {
                            "cause": "老王反馈 Safari 白屏",
                            "effect": "我承诺周四提交修复方案",
                            "relation": "caused",
                            "task_tag": "登录页",
                        }
                    ],
                    "decisions": [
                        {
                            "what": "登录页先兼容 Safari",
                            "rationale": "用户投诉集中",
                            "actor": "owner",
                        }
                    ],
                    "degraded": False,
                },
                ensure_ascii=False,
            ),
            cause_count=1,
            commitment_count=1,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(episode)
        session.flush()

        session.add(
            models.Commitment(
                episode_id=episode.id,
                recording_id=rec.id,
                what="提交登录页 Safari 白屏修复方案",
                owner="我",
                counterparty="老王",
                due_text="周四",
                confidence=0.9,
            )
        )
        session.add(
            models.Segment(
                recording_id=rec.id,
                idx=0,
                start_ms=0,
                end_ms=3000,
                text="周四之前我给你一个方案",
                speaker="owner",
            )
        )
        session.commit()

    card = client.get("/v1/context/brief", headers=AUTH).text
    assert "进行中的承诺" in card
    assert "提交登录页 Safari 白屏修复方案" in card
    assert "老王" in card
    assert "关键决策" in card
    assert "因果脉络" in card

    # and the structured endpoint agrees
    rows = client.get("/v1/commitments", headers=AUTH).json()
    assert rows["count"] == 1
    assert rows["commitments"][0]["what"] == "提交登录页 Safari 白屏修复方案"


def test_brief_respects_token_budget(client, clean_db):
    """Trimming must cut whole sections from the tail, never mid-sentence."""
    from sqlmodel import Session

    from shadowscribe import models

    with Session(clean_db) as session:
        rec = models.Recording(
            client_id="pixel-8",
            recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            status="done",
        )
        session.add(rec)
        session.flush()

        for index in range(4):
            episode = models.Episode(
                recording_id=rec.id,
                window_index=index,
                title=f"第 {index} 场会议的主题",
                summary="这是一段相当长的会议摘要，用来把上下文卡片的体积推到预算之上。" * 4,
                raw_json=json.dumps(
                    {
                        "edges": [
                            {
                                "cause": f"第{index}场会议的起因{i}描述文字",
                                "effect": f"第{index}场会议的结果{i}描述文字",
                                "relation": "caused",
                                "task_tag": f"标签{i}",
                            }
                            for i in range(6)
                        ],
                        "decisions": [],
                        "degraded": False,
                    },
                    ensure_ascii=False,
                ),
                cause_count=6,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add(episode)
            session.flush()
            for i in range(4):
                session.add(
                    models.Commitment(
                        episode_id=episode.id,
                        recording_id=rec.id,
                        what=f"第{index}场会议承诺的第{i}项待办事项内容",
                        counterparty="老王",
                        due_text="周四",
                        confidence=0.9,
                    )
                )
        session.commit()

    small = client.get("/v1/context/brief", headers=AUTH, params={"max_tokens": 300}).text
    large = client.get("/v1/context/brief", headers=AUTH, params={"max_tokens": 8000}).text

    assert len(small) < len(large)
    assert "已按 token 预算截断" in small
    # the highest-value section survives any budget
    assert "进行中的承诺" in small
    assert "进行中的承诺" in large
    # and the tail is what gets dropped
    assert "话题片段" in large


def test_brief_shows_episode_frame_when_distillation_degraded(client, clean_db):
    """No LLM key → no edges, but the card must still locate the day."""
    from sqlmodel import Session

    from shadowscribe import models

    with Session(clean_db) as session:
        rec = models.Recording(
            client_id="pixel-8",
            recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            status="done",
        )
        session.add(rec)
        session.flush()
        session.add(
            models.Episode(
                recording_id=rec.id,
                title="与老王在会议室",
                summary="[1] 未知(00:00): 这个 Safari 的问题得先解决",
                raw_json=json.dumps({"edges": [], "decisions": [], "degraded": True}),
                cause_count=0,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        session.commit()

    card = client.get("/v1/context/brief", headers=AUTH).text
    assert "话题片段" in card
    assert "与老王在会议室" in card
    assert "未蒸馏" in card


def test_brief_default_window_excludes_old_recordings(client, clean_db):
    from sqlmodel import Session

    from shadowscribe import models

    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    with Session(clean_db) as session:
        session.add(
            models.Recording(client_id="pixel-8", recorded_at=old, status="done", duration_ms=1000)
        )
        session.commit()

    assert "没有已处理的录音" in client.get("/v1/context/brief", headers=AUTH).text
    wide = client.get("/v1/context/brief", headers=AUTH, params={"hours": 24 * 20}).text
    assert "没有已处理的录音" not in wide
