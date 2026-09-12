#!/usr/bin/env bash
# Cookie MVP smoke: import → status → export roundtrip → clear
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export CLOAKCLI_HOME="$ROOT"
BIN="${CLOAKCLI_BIN:-$ROOT/target/debug/cloakcli}"

if [[ ! -x "$BIN" ]]; then
  cargo build -q
  BIN="$ROOT/target/debug/cloakcli"
fi

PROF="cookietest"
# recreate profile
"$BIN" profile delete "$PROF" 2>/dev/null || true
"$BIN" profile create "$PROF" --notes "cookie mvp verify" >/dev/null

FIX="$ROOT/fixtures/sample-cookies.json"
"$BIN" profile cookie import "$PROF" "$FIX" --format auto
STATUS=$("$BIN" profile cookie status "$PROF")
echo "$STATUS" | grep -q '"cookie_count": 2'
echo "$STATUS" | grep -q '"valid_count"'
echo "$STATUS" | grep -q 'example.com'
# must not leak secret value in status
if echo "$STATUS" | grep -q 'TEST_SECRET_VALUE'; then
  echo "FAIL: status leaked cookie value" >&2
  exit 1
fi

# export --out must NOT write into project data/ (Astra policy)
if "$BIN" profile cookie export "$PROF" --out "$ROOT/data/cookie-export-test.json" 2>/tmp/cloakcli-export-err.txt; then
  echo "FAIL: export into data/ should be rejected" >&2
  exit 1
fi
grep -qi 'refuses\|data/' /tmp/cloakcli-export-err.txt

# Allowed target outside data/ (repo-root sibling file ok) + 0600 on new + existing
OUT="$ROOT/cookie-export-verify.json"
rm -f "$OUT"
"$BIN" profile cookie export "$PROF" --out "$OUT"
MODE=$(stat -c '%a' "$OUT" 2>/dev/null || stat -f '%Lp' "$OUT")
if [[ "$MODE" != "600" ]]; then
  echo "FAIL: export mode is $MODE want 600" >&2
  exit 1
fi
# overwrite existing 0644 → must reset 0600
chmod 644 "$OUT"
"$BIN" profile cookie export "$PROF" --out "$OUT"
MODE2=$(stat -c '%a' "$OUT" 2>/dev/null || stat -f '%Lp' "$OUT")
if [[ "$MODE2" != "600" ]]; then
  echo "FAIL: re-export mode is $MODE2 want 600" >&2
  exit 1
fi
# roundtrip content has cookies array
grep -q '"sessionid"' "$OUT"
# layout
test -f "$ROOT/profiles/$PROF/profile.json"
test -f "$ROOT/profiles/$PROF/cookie.json"
CKMODE=$(stat -c '%a' "$ROOT/profiles/$PROF/cookie.json")
[[ "$CKMODE" == "600" ]]

"$BIN" profile cookie clear "$PROF"
STATUS2=$("$BIN" profile cookie status "$PROF")
echo "$STATUS2" | grep -q '"present": false'
test ! -f "$ROOT/profiles/$PROF/cookie.json"

# flat → dir migration on status (not only import)
FLATPROF="cookiefat"
"$BIN" profile delete "$FLATPROF" 2>/dev/null || true
mkdir -p "$ROOT/profiles"
# create via CLI then flatten artificially
"$BIN" profile create "$FLATPROF" --notes "flat migrate" >/dev/null
# move profile.json back to flat layout
if [[ -f "$ROOT/profiles/$FLATPROF/profile.json" ]]; then
  mv "$ROOT/profiles/$FLATPROF/profile.json" "$ROOT/profiles/$FLATPROF.json"
  rmdir "$ROOT/profiles/$FLATPROF" 2>/dev/null || rm -rf "$ROOT/profiles/$FLATPROF"
fi
test -f "$ROOT/profiles/$FLATPROF.json"
"$BIN" profile cookie status "$FLATPROF" >/dev/null
test -f "$ROOT/profiles/$FLATPROF/profile.json"
test ! -f "$ROOT/profiles/$FLATPROF.json"
"$BIN" profile delete "$FLATPROF" 2>/dev/null || true

# re-import for optional open check
"$BIN" profile cookie import "$PROF" "$FIX" --format storage-state >/dev/null

# corrupt cookie must make open-path resolver fail (Rust cookie_file_for_open)
printf '{not json' > "$ROOT/profiles/$PROF/cookie.json"
chmod 600 "$ROOT/profiles/$PROF/cookie.json"
if "$BIN" profile cookie status "$PROF" >/tmp/cloakcli-status-corrupt.txt 2>&1; then
  # status may error — ensure no secret leak either way
  :
fi
if grep -q 'TEST_SECRET_VALUE' /tmp/cloakcli-status-corrupt.txt 2>/dev/null; then
  echo "FAIL: corrupt status leaked value" >&2
  exit 1
fi
# restore valid
"$BIN" profile cookie import "$PROF" "$FIX" --format auto >/dev/null

echo "OK cookie verify (import/status/export/clear/layout/0600/migrate/export-policy)"
rm -f "$OUT" /tmp/cloakcli-export-err.txt /tmp/cloakcli-status-corrupt.txt
