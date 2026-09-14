#!/bin/bash
# CloakCLI TUI launcher for macOS Terminal.app (Mac mini).
#
# ratatui cannot set the terminal font size. This script sets the front
# Terminal window to 18pt via AppleScript (readable range: 16–20) before
# exec'ing the release binary.
set -euo pipefail
cd "$(dirname "$0")"

export COLORTERM=truecolor
export TERM=xterm-256color

# Drop a previous TUI occupying another tab/window. Does not match the worker.
pkill -f 'cloakcli tui' 2>/dev/null || true

# Front window is this .command's Terminal.app session when double-clicked.
if command -v osascript >/dev/null 2>&1; then
  osascript -e 'tell application "Terminal" to set font size of front window to 18' \
    >/dev/null 2>&1 || true
fi

BIN="./target/release/cloakcli"
if [[ ! -x "$BIN" ]]; then
  echo "missing $BIN — run: cargo build --release" >&2
  exit 1
fi
exec "$BIN" tui
