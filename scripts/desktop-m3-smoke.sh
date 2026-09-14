#!/usr/bin/env bash
# Geek Desktop M3 smoke: unit tests for runs history, resume hint, LLM status,
# redaction, and the frontend shell. Does not start a headed WebView.
#
#   ./scripts/desktop-m3-smoke.sh
#
# Live UI (Mac / this box):
#   cargo build
#   cd desktop && npm install
#   export CLOAKCLI_BIN="$(cd .. && pwd)/target/debug/cloakcli"
#   export CLOAKCLI_HOME="$(cd .. && pwd)"
#   npm run tauri dev
# Then: Chat → Start → send a goal → job card History; Settings (,); ? help;
# Diagnostics (5) still opens Raw TUI. Reconnect uses data/teach/events-snapshot.json.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== cloakcli tests (offline) =="
cargo test --offline

echo "== desktop src-tauri tests (offline) =="
(cd desktop/src-tauri && cargo test --offline)

echo "== desktop frontend tests =="
(cd desktop && npm test)

echo "== desktop vite build =="
(cd desktop && npm run build)

echo "M3 smoke OK"
