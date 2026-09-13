# shadowscribe (server)

The ingestion + distillation server — and the MCP endpoint — for
[影书 ShadowScribe](https://github.com/yw1103/ShadowScribe).

This is where everything lives. The desktop only needs a URL pointing at `/mcp`.

Two entry points, one codebase:

```bash
shadowscribe serve      # HTTP API + /mcp  (uvicorn shadowscribe.main:app)
shadowscribe worker     # pipeline worker (ASR → diarization → distillation → memory)
```

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[memory,speakers,mcp,dev]'

cp ../.env.example .env      # set SS_TOKEN and SS_LLM_API_KEY
shadowscribe doctor          # verify ffmpeg / whisper / memory / LLM / voiceprint
shadowscribe serve &         # API + MCP on :18080
shadowscribe worker &        # drain the queue
```

Verify without a phone:

```bash
shadowscribe ingest ./sample.m4a --hint "与老王在会议室"
shadowscribe brief
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/mcp   # expect 200
```

## Layout

| Path | Responsibility |
|---|---|
| `shadowscribe/api.py` | HTTP surface: ingest, resumable uploads, recall, voiceprint; mounts `/mcp` |
| `shadowscribe/mcp_surface.py` | **The MCP endpoint.** Streamable HTTP at `/mcp`, both tool families, no child process |
| `shadowscribe/service.py` | Read model — `build_brief()` is the product |
| `shadowscribe/pipeline/audio.py` | ffmpeg normalisation to 16 kHz mono PCM |
| `shadowscribe/pipeline/asr.py` | faster-whisper transcription |
| `shadowscribe/pipeline/diarize.py` | Voiceprint owner/guest attribution |
| `shadowscribe/pipeline/text.py` | Traditional → Simplified normalisation (retrieval consistency) |
| `shadowscribe/pipeline/distill.py` | LLM extraction of causes, decisions, commitments |
| `shadowscribe/pipeline/runner.py` | Orchestration of the whole chain |
| `shadowscribe/memory/` | Pluggable long-term store (`causal-memory` or native SQLite) |
| `shadowscribe/worker.py` | SQLite-backed job queue drain loop |

## The MCP endpoint

Mounted by `mount_mcp()` in `create_app()`. Two things there are load-bearing:

- **Not `app.mount()`.** Starlette compiles a mount to `^/mcp/(?P<path>.*)$`, which
  never matches the bare `/mcp` an editor puts in its config; the router answers
  307, and whether an MCP client follows that on a POST carrying a JSON-RPC body
  is undefined. An explicit `Route` plus an internal path rewrite handles both.
- **Lifespan composition.** Starlette does not run a child app's lifespan, so the
  streamable-HTTP session manager has to be started from the parent's.

It exposes both tool families because they read different stores: commitments,
transcripts and daily episodes live in ShadowScribe's own SQLite and never enter
`causal-memory`'s graph.

## Configuration

Every setting is an `SS_*` environment variable — see [`../.env.example`](../.env.example)
for the annotated list. The ones that matter most:

| Variable | Why |
|---|---|
| `SS_TOKEN` | Bearer token for every `/v1` route. Required outside a laptop. |
| `SS_LLM_API_KEY` | Without it, distillation degrades to transcript-only episodes. |
| `SS_WHISPER_MODEL` | `small` boots in seconds; `large-v3` is much better on Chinese. |
| `SS_DIARIZATION` | `embedding` enables voiceprint owner/guest attribution. |
| `SS_MCP_REQUIRE_TOKEN` | `false` for a single-user box; `true` before exposing `/mcp`. |
| `SS_MEMORY_BACKEND` | `causal-memory` (full causal graph) or `native` (zero deps). |

Build-time only: `INSTALL_MCP` (default `true` — without it the desktop cannot
connect), `INSTALL_SPEAKERS`, `INSTALL_MEMORY_BACKEND`, `APT_MIRROR`,
`PIP_INDEX_URL`.

## Tests

```bash
pip install -e '.[dev]'
pytest -q          # 145 tests; no ffmpeg, no models, no API key
```
