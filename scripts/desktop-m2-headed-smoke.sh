#!/usr/bin/env bash
# Geek Desktop M2 headed evidence: Teach Chat → browser action → result echo.
#
# Requires: cargo-built cloakcli, CloakBrowser Chromium, DISPLAY (or xvfb-run).
#
#   ./scripts/desktop-m2-headed-smoke.sh
#   CLOAKCLI_BIN=target/debug/cloakcli ./scripts/desktop-m2-headed-smoke.sh
#
# Writes artifacts/m2-headed-*.jsonl|log|png
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

exec python3 "$ROOT/scripts/desktop-m2-headed-smoke.py"
