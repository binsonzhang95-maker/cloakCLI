#!/usr/bin/env bash
# Geek Desktop M2 smoke: JSONL teach-chat (no headed browser) + desktop unit tests.
#
#   ./scripts/desktop-m2-smoke.sh
#   CLOAKCLI_BIN=target/release/cloakcli ./scripts/desktop-m2-smoke.sh
#
# Manual (Mac / this box), live UI:
#   cargo build
#   cd desktop && npm install
#   export CLOAKCLI_BIN="$(cd .. && pwd)/target/debug/cloakcli"
#   export CLOAKCLI_HOME="$(cd .. && pwd)"
#   npm run tauri dev
# Then: Teach Chat → Start (uncheck browser if no display) → send a goal.
# Diagnostics (5) still opens Raw TUI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BIN="${CLOAKCLI_BIN:-$ROOT/target/debug/cloakcli}"
if [[ ! -x "$BIN" ]]; then
  echo "building cloakcli…"
  cargo build --offline
  BIN="$ROOT/target/debug/cloakcli"
fi

HOME_DIR="$(mktemp -d /tmp/cloakcli-m2-smoke-XXXXXX)"
cleanup() { rm -rf "$HOME_DIR"; }
trap cleanup EXIT

mkdir -p "$HOME_DIR/skills"
printf '[package]\nname="t"\nversion="0.0.0"\n' >"$HOME_DIR/Cargo.toml"

"$BIN" --help >/dev/null
CLOAKCLI_HOME="$HOME_DIR" "$BIN" profile create demo >/dev/null

MOCK='{"schema_version":1,"actions":[{"action":"click","selector":"a"},{"action":"done","reason":"ok"}]}'
OUT="$(mktemp)"
ERR="$(mktemp)"
{
  printf '%s\n' '{"cmd":"send","goal":"click the link token=abc123SECRETVALUE","profile":"demo"}'
  printf '%s\n' '{"cmd":"stop"}'
} | CLOAKCLI_HOME="$HOME_DIR" "$BIN" teach chat \
    --profile demo --events --no-browser --mock-json "$MOCK" \
    >"$OUT" 2>"$ERR" || true

python3 - "$OUT" "$ERR" <<'PY'
import json, sys
out, err = open(sys.argv[1]).read(), open(sys.argv[2]).read()
blob = out + err
if "abc123SECRETVALUE" in blob:
    print("FAIL: secret leaked")
    sys.exit(1)
kinds = []
for line in out.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        v = json.loads(line)
    except json.JSONDecodeError:
        continue
    kinds.append(v.get("kind"))
need = {"session", "user", "assistant", "job", "closed"}
missing = need - set(kinds)
if missing:
    print("FAIL: missing event kinds", missing)
    print(out[:4000])
    sys.exit(1)
print("ok events:", ",".join(k for k in kinds if k))
PY

echo "desktop crate tests…"
(cd "$ROOT/desktop/src-tauri" && cargo test --offline)

echo "frontend tests…"
(cd "$ROOT/desktop" && npm test)

echo "frontend build…"
(cd "$ROOT/desktop" && npm run build)

echo "PASS desktop M2 smoke"
