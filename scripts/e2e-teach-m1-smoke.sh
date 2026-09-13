#!/usr/bin/env bash
# Teach Chat M1 headed smoke (Astra M1 blocker).
#
# Real headed CloakBrowser + MV3 extension + Python worker + Rust hub:
#   - extension and worker each pair (same session)
#   - page_state: url / origin / title / viewport / observation_id
#   - non-allowlist origin: no content inject, no page_state
#   - extension service-worker restart reuses the same session
#   - worker reconnect + duplicate pairing rejection
#   - run logs scanned for token / cookie / password leakage
#
# Requires: cargo-built cloakcli, CloakBrowser Chromium, DISPLAY or xvfb.
#
#   ./scripts/e2e-teach-m1-smoke.sh
#   CLOAKCLI_BIN=target/release/cloakcli ./scripts/e2e-teach-m1-smoke.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    echo "no DISPLAY; retrying under xvfb-run"
    exec xvfb-run -a -s "-screen 0 1280x720x24" "$0" "$@"
  fi
  echo "FAIL: no DISPLAY/WAYLAND_DISPLAY and xvfb-run not found" >&2
  exit 2
fi

export CLOAKCLI_HOME="${CLOAKCLI_HOME:-$ROOT}"
export PYTHONUNBUFFERED=1
export PYTHONPATH="${ROOT}/python${PYTHONPATH:+:$PYTHONPATH}"

BIN="${CLOAKCLI_BIN:-$ROOT/target/debug/cloakcli}"
if [[ ! -x "$BIN" ]]; then
  echo "building cloakcli…"
  cargo build --offline
  BIN="$ROOT/target/debug/cloakcli"
fi
export CLOAKCLI_BIN="$BIN"

exec python3 "$ROOT/scripts/teach_m1_headed_smoke.py"
