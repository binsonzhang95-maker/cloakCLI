#!/usr/bin/env bash
# Build the geek desktop shell for the current platform.
#
# Linux (this box): produces a .deb under desktop/src-tauri/target/release/bundle/deb/
# macOS (Mac mini): produces an unsigned .app under
#   desktop/src-tauri/target/release/bundle/macos/
#
# Prerequisites: see desktop/README.md (WebKitGTK 4.1 on Linux; Xcode CLT on Mac).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "${CLOAKCLI_BIN:-}" ]]; then
  echo "building cloakcli (release)…"
  cargo build --release --offline 2>/dev/null || cargo build --release
  export CLOAKCLI_BIN="$ROOT/target/release/cloakcli"
fi
export CLOAKCLI_HOME="${CLOAKCLI_HOME:-$ROOT}"

cd "$ROOT/desktop"
if [[ ! -d node_modules ]]; then
  npm install
fi
npm run tauri build

echo
echo "Bundle output:"
find src-tauri/target/release/bundle -type f \( -name '*.deb' -o -name '*.AppImage' -o -name '*.dmg' -o -name 'CloakCLI' -o -name '*.app' \) 2>/dev/null | head -40
echo "Done. Sidecar cloakcli is NOT bundled; set CLOAKCLI_BIN / PATH. Unsigned."
