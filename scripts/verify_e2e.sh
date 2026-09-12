#!/usr/bin/env bash
#
# End-to-end verification against a running ShadowScribe server.
#
# Exercises the real phone-facing HTTP API (not the CLI), then waits for the
# worker and prints the context card. Use this to prove a deployment works
# before wiring up a phone client.
#
#   SS_TOKEN=... ./scripts/verify_e2e.sh ./sample.m4a
#   BASE=http://127.0.0.1:18080 SS_TOKEN=... ./scripts/verify_e2e.sh ./sample.m4a --hint "与老王在会议室"
#
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:18080}"
AUDIO="${1:-}"
HINT=""
RECORDED_AT=""
TIMEOUT_S="${TIMEOUT_S:-900}"

shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --hint) HINT="$2"; shift 2 ;;
    --recorded-at) RECORDED_AT="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$AUDIO" ] || [ ! -f "$AUDIO" ]; then
  echo "usage: SS_TOKEN=... $0 <audio-file> [--hint TEXT] [--recorded-at ISO8601]" >&2
  exit 2
fi
if [ -z "${SS_TOKEN:-}" ]; then
  echo "SS_TOKEN is required" >&2
  exit 2
fi

AUTH="Authorization: Bearer $SS_TOKEN"
json() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1',''))"; }

echo "==> server reachable?"
curl -fsS --max-time 10 "$BASE/healthz" | python3 -m json.tool
echo

echo "==> ingest $AUDIO"
ARGS=(-s --max-time 600 -X POST "$BASE/v1/ingest/audio" -H "$AUTH"
      -F "file=@$AUDIO" -F "client_id=verify" -F "device=verify-e2e")
[ -n "$HINT" ] && ARGS+=(-F "session_hint=$HINT")
[ -n "$RECORDED_AT" ] && ARGS+=(-F "recorded_at=$RECORDED_AT")

RESP=$(curl "${ARGS[@]}")
echo "$RESP"
JOB=$(echo "$RESP" | json job_id)
REC=$(echo "$RESP" | json recording_id)
if [ -z "$REC" ]; then echo "ingest failed" >&2; exit 1; fi

echo
echo "==> idempotency: re-uploading identical bytes must dedup"
curl -s --max-time 600 -X POST "$BASE/v1/ingest/audio" -H "$AUTH" \
  -F "file=@$AUDIO" -F "client_id=verify" | python3 -m json.tool

echo
echo "==> waiting for the worker (timeout ${TIMEOUT_S}s)"
deadline=$(( $(date +%s) + TIMEOUT_S ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  LINE=$(curl -s --max-time 20 -H "$AUTH" "$BASE/v1/jobs/$JOB" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('status'), '|', d.get('error') or '', '|', json.dumps(d.get('result'), ensure_ascii=False) if d.get('result') else '')")
  echo "    $LINE"
  case "$LINE" in
    done*) break ;;
    failed*) echo "job failed" >&2; exit 1 ;;
  esac
  sleep 6
done

echo
echo "==> transcript"
curl -s --max-time 30 -H "$AUTH" "$BASE/v1/recordings/$REC" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('status:', d['status'], '| lang:', d.get('language'), '| duration_ms:', d.get('duration_ms'))
for e in d.get('episodes', []):
    print(f\"  episode: {e['title']} | topics={e['topics']} | causes={e['causes']} | commitments={e['commitments']}\")
print('--- segments ---')
for s in (d.get('segments') or []):
    print(f\"  [{s['start_ms']//1000:>4}s] {s.get('speaker')}: {s['text']}\")
"

echo
echo "==> context card"
curl -s --max-time 30 -H "$AUTH" "$BASE/v1/context/brief?hours=720"

echo
echo "==> commitments"
curl -s --max-time 30 -H "$AUTH" "$BASE/v1/commitments" \
  | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))"

echo
echo "==> search"
curl -s --max-time 30 -H "$AUTH" "$BASE/v1/search?q=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "${SEARCH_Q:-登录页}")" \
  | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))"

echo
echo "==> E2E OK"
