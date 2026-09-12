# shadowscribe (server)

The ingestion + distillation server for [影书 ShadowScribe](https://github.com/yw1103/ShadowScribe).

Two entry points, one codebase:

```bash
shadowscribe serve      # HTTP API  (uvicorn shadowscribe.main:app)
shadowscribe worker     # pipeline worker (ASR → diarization → distillation → memory)
```

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[memory,speakers,dev]'

cp ../.env.example .env      # set SS_TOKEN and SS_LLM_API_KEY
shadowscribe doctor          # verify ffmpeg / whisper / memory / LLM
shadowscribe serve &         # API on :18080
shadowscribe worker &        # drain the queue
```

Verify without a phone:

```bash
shadowscribe ingest ./sample.m4a --hint "与老王在会议室"
shadowscribe brief
```

## Layout

| Path | Responsibility |
|---|---|
| `shadowscribe/api.py` | HTTP surface: ingest, resumable uploads, recall, voiceprint |
| `shadowscribe/service.py` | Read model — `build_brief()` is the product |
| `shadowscribe/pipeline/audio.py` | ffmpeg normalisation to 16 kHz mono PCM |
| `shadowscribe/pipeline/asr.py` | faster-whisper transcription |
| `shadowscribe/pipeline/diarize.py` | Voiceprint owner/guest attribution |
| `shadowscribe/pipeline/distill.py` | LLM extraction of causes, decisions, commitments |
| `shadowscribe/pipeline/runner.py` | Orchestration of the whole chain |
| `shadowscribe/memory/` | Pluggable long-term store (`causal-memory` or native SQLite) |
| `shadowscribe/worker.py` | SQLite-backed job queue drain loop |

## Configuration

Every setting is an `SS_*` environment variable — see [`../.env.example`](../.env.example)
for the annotated list. The four that matter most:

| Variable | Why |
|---|---|
| `SS_TOKEN` | Bearer token for every `/v1` route. Required outside a laptop. |
| `SS_LLM_API_KEY` | Without it, distillation degrades to transcript-only episodes. |
| `SS_WHISPER_MODEL` | `small` boots in seconds; `large-v3` is much better on Chinese. |
| `SS_MEMORY_BACKEND` | `causal-memory` (full causal graph) or `native` (zero deps). |
