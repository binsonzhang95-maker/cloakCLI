#!/usr/bin/env bash
# Minimal fleet DEV STUB e2e:
#   master serve → client connect → pack+publish → skill_sync ACK → digest-bound submit
# NOT a production security test. Plaintext shared token. Digest ≠ TLS.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export CLOAKCLI_HOME="$ROOT"
BIN="${CLOAKCLI_BIN:-$ROOT/target/debug/cloakcli}"
TOKEN="${CLOAKCLI_MASTER_TOKEN:-dev-token}"
BIND="127.0.0.1:7750"
CLIENT_ID="e2e-box1"

if [[ ! -x "$BIN" ]]; then
  echo "building…"
  cargo build
  BIN="$ROOT/target/debug/cloakcli"
fi

# Clean stale control socket / prior e2e pids (avoid pkill -f; it can match this script's argv)
for f in /tmp/cloakcli-master-e2e.pid /tmp/cloakcli-client-e2e.pid; do
  if [[ -f "$f" ]]; then
    kill "$(cat "$f")" 2>/dev/null || true
    rm -f "$f"
  fi
done
sleep 0.3
rm -f data/master_ctrl.sock
rm -f data/jobs/e2e-hello-1.json data/jobs/e2e-echo-1.json

echo "== master serve =="
"$BIN" master serve --bind "$BIND" --token "$TOKEN" > /tmp/cloakcli-master-e2e.log 2>&1 &
MASTER_PID=$!
echo "$MASTER_PID" > /tmp/cloakcli-master-e2e.pid
cleanup() {
  kill "$MASTER_PID" ${CLIENT_PID:-} 2>/dev/null || true
  wait "$MASTER_PID" ${CLIENT_PID:-} 2>/dev/null || true
  rm -f /tmp/cloakcli-master-e2e.pid /tmp/cloakcli-client-e2e.pid
}
trap cleanup EXIT

for i in $(seq 1 30); do
  [[ -S data/master_ctrl.sock ]] && break
  sleep 0.1
done
[[ -S data/master_ctrl.sock ]] || { echo "master control sock missing"; cat /tmp/cloakcli-master-e2e.log; exit 1; }

echo "== client connect =="
"$BIN" client connect --master "$BIND" --id "$CLIENT_ID" --token "$TOKEN" > /tmp/cloakcli-client-e2e.log 2>&1 &
CLIENT_PID=$!
echo "$CLIENT_PID" > /tmp/cloakcli-client-e2e.pid

echo "== wait for client online =="
ONLINE=0
for i in $(seq 1 40); do
  OUT=$("$BIN" master clients 2>/dev/null || true)
  if echo "$OUT" | grep -q "$CLIENT_ID"; then
    ONLINE=1
    echo "$OUT"
    break
  fi
  sleep 0.25
done
[[ "$ONLINE" = 1 ]] || { echo "client never registered"; cat /tmp/cloakcli-client-e2e.log; exit 1; }

echo "== config_update (persist + push) =="
"$BIN" master config --concurrency 3 --headless
"$BIN" master config --get
# desired file must exist
test -f data/hub_desired.json
echo "hub_desired.json:"
cat data/hub_desired.json
# observed should catch up via config_ack (not stuck at hello rev=0)
sleep 0.5
OBS=$("$BIN" master clients)
echo "$OBS"
echo "$OBS" | grep -q '"observed_revision": [1-9]' || echo "$OBS" | grep -q '"observed_revision": [1-9][0-9]*' || {
  echo "WARN: observed_revision still 0 right after config; waiting heartbeat…"
  sleep 16
  OBS=$("$BIN" master clients)
  echo "$OBS"
}

echo "== pack + publish skills =="
# Ensure noproxy profile exists
"$BIN" profile list >/dev/null 2>&1 || true
if ! "$BIN" profile list 2>/dev/null | grep -q noproxy; then
  "$BIN" profile create noproxy || true
fi
"$BIN" master skill-pack --skill echo-runner
"$BIN" master skill-pack --skill hello
echo "== skill-sync echo-runner + hello =="
"$BIN" master skill-sync --client "$CLIENT_ID" --skill echo-runner
"$BIN" master skill-sync --client "$CLIENT_ID" --skill hello

echo "== wrong digest refused =="
if "$BIN" master submit --client "$CLIENT_ID" --skill echo-runner --profile noproxy --headless \
    --digest aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    --job-id e2e-bad-digest; then
  echo "expected digest reject"; exit 1
fi

echo "== submit echo-runner (python_runner, digest-bound) =="
ECHO_JSON=$("$BIN" master submit --client "$CLIENT_ID" --skill echo-runner --profile noproxy --headless --job-id e2e-echo-1)
echo "$ECHO_JSON"
echo "$ECHO_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok') is True and d.get('digest'), d"

echo "== poll echo job_state =="
ECHO_FINAL=""
for i in $(seq 1 30); do
  ST=$("$BIN" master job-state --job-id e2e-echo-1 2>/dev/null || true)
  echo "$ST" | head -c 400; echo
  if echo "$ST" | grep -Eq '"state": "(succeeded|failed|cancelled)"'; then
    ECHO_FINAL="$ST"
    break
  fi
  sleep 0.25
done
[[ -n "$ECHO_FINAL" ]] || { echo "echo-runner job did not finish"; cat /tmp/cloakcli-client-e2e.log; exit 1; }
echo "$ECHO_FINAL" | grep -q '"state": "succeeded"' || { echo "echo-runner did not succeed"; exit 1; }

echo "== submit hello =="
JOB_JSON=$("$BIN" master submit --client "$CLIENT_ID" --skill hello --profile noproxy --headless --job-id e2e-hello-1)
echo "$JOB_JSON"
JOB_ID=$(echo "$JOB_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))")
[[ -n "$JOB_ID" ]] || { echo "no job_id"; exit 1; }

echo "== poll job_state =="
FINAL=""
for i in $(seq 1 60); do
  ST=$("$BIN" master job-state --job-id "$JOB_ID" 2>/dev/null || true)
  echo "$ST" | head -c 400; echo
  if echo "$ST" | grep -Eq '"state": "(succeeded|failed|cancelled)"'; then
    FINAL="$ST"
    break
  fi
  sleep 1
done
[[ -n "$FINAL" ]] || { echo "job did not finish"; cat /tmp/cloakcli-client-e2e.log; exit 1; }

echo "== idempotent resubmit same job_id =="
"$BIN" master submit --client "$CLIENT_ID" --skill hello --profile noproxy --headless --job-id "$JOB_ID"

echo "E2E OK (fleet DEV STUB)"
