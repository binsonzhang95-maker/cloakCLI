#!/usr/bin/env bash
# Geek Desktop M2 smoke: JSONL teach-chat (no headed browser) + desktop unit tests.
#
# Covers: incremental assistant_delta, cancel mid-stream, child-exit resume,
# unknown-cmd rejection, redaction. Headed Teach Chat → browser action is
# `scripts/desktop-m2-headed-smoke.sh` (not this mock path).
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

python3 - "$BIN" "$HOME_DIR" "$MOCK" <<'PY'
import json, os, subprocess, sys, time
bin_path, home, mock = sys.argv[1], sys.argv[2], sys.argv[3]
env = os.environ.copy()
env["CLOAKCLI_HOME"] = home
env["CLOAKCLI_TEACH_STREAM_CHUNK_MS"] = "8"
env["CLOAKCLI_TEACH_STREAM_CHUNK_CHARS"] = "6"

def spawn():
    return subprocess.Popen(
        [bin_path, "teach", "chat", "--profile", "demo", "--events", "--no-browser", "--mock-json", mock],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )

def send(p, obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()

def read_until(p, pred, timeout=12.0, collected=None):
    collected = collected if collected is not None else []
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
        except json.JSONDecodeError:
            continue
        collected.append(v)
        if pred(v, collected):
            return collected
    return collected

def kinds(events):
    return [e.get("kind") for e in events if e.get("kind")]

p = spawn()
events = read_until(p, lambda v, _c: v.get("kind") == "session")
if not events or events[0].get("kind") != "session":
    err = p.stderr.read() if p.stderr else ""
    print("FAIL: no session event", events, err[:2000])
    sys.exit(1)

send(p, {"cmd": "send", "goal": "click the link token=abc123SECRETVALUE", "profile": "demo"})
events = read_until(
    p,
    lambda v, c: v.get("kind") == "assistant" and v.get("done") is True,
    collected=events,
)
send(p, {"cmd": "stop"})
try:
    p.stdin.close()
except Exception:
    pass
try:
    p.wait(timeout=8)
except subprocess.TimeoutExpired:
    p.kill()
    p.wait(timeout=2)

blob = json.dumps(events)
err = p.stderr.read() if p.stderr else ""
if "abc123SECRETVALUE" in blob or "abc123SECRETVALUE" in err:
    print("FAIL: secret leaked")
    sys.exit(1)
need = {"session", "user", "assistant_delta", "assistant", "job", "closed"}
missing = need - set(kinds(events))
# closed may arrive after we stopped reading; tolerate if process exited
if "closed" in missing:
    missing.remove("closed")
if missing:
    print("FAIL: missing event kinds", missing)
    print(json.dumps(events, indent=2)[:4000])
    sys.exit(1)
deltas = [e for e in events if e.get("kind") == "assistant_delta"]
if len(deltas) < 2:
    print("FAIL: expected incremental assistant_delta chunks, got", len(deltas))
    sys.exit(1)
print("ok events:", ",".join(k for k in kinds(events) if k))

# unknown cmd
p = spawn()
read_until(p, lambda v, _c: v.get("kind") == "session")
send(p, {"cmd": "explode", "shell": "rm -rf /"})
bad = read_until(p, lambda v, _c: v.get("kind") == "error")
send(p, {"cmd": "stop"})
try:
    p.stdin.close()
except Exception:
    pass
p.wait(timeout=8)
if not any(e.get("code") == "bad_cmd" for e in bad):
    print("FAIL: unknown cmd not rejected", bad[-5:])
    sys.exit(1)
print("ok unknown cmd rejected")

# cancel mid-stream
env["CLOAKCLI_TEACH_STREAM_CHUNK_MS"] = "35"
env["CLOAKCLI_TEACH_STREAM_CHUNK_CHARS"] = "4"
p = spawn()
read_until(p, lambda v, _c: v.get("kind") == "session")
send(p, {"cmd": "send", "goal": "click go", "profile": "demo"})
got = read_until(p, lambda v, _c: v.get("kind") == "assistant_delta")
if not any(e.get("kind") == "assistant_delta" for e in got):
    print("FAIL: no delta before cancel")
    sys.exit(1)
send(p, {"cmd": "cancel"})
got = read_until(p, lambda v, _c: v.get("kind") == "job" and v.get("state") == "cancelled", collected=got)
send(p, {"cmd": "stop"})
try:
    p.stdin.close()
except Exception:
    pass
p.wait(timeout=8)
if not any(e.get("kind") == "job" and e.get("state") == "cancelled" for e in got):
    print("FAIL: cancel mid-stream did not emit cancelled job", kinds(got))
    sys.exit(1)
print("ok cancel mid-stream")

# child-exit reconnect / resume
env["CLOAKCLI_TEACH_STREAM_CHUNK_MS"] = "0"
p = spawn()
ev = read_until(p, lambda v, _c: v.get("kind") == "session")
send(p, {"cmd": "send", "goal": "click the link", "profile": "demo"})
ev = read_until(p, lambda v, c: v.get("kind") == "assistant" and v.get("done") is True, collected=ev)
send(p, {"cmd": "stop"})
try:
    p.stdin.close()
except Exception:
    pass
p.wait(timeout=8)
snap = os.path.join(home, "data", "teach", "events-snapshot.json")
if not os.path.isfile(snap):
    print("FAIL: snapshot not written", snap)
    sys.exit(1)

p = spawn()
ev = read_until(p, lambda v, _c: v.get("kind") == "resume", timeout=8)
if not any(e.get("kind") == "resume" for e in ev):
    print("FAIL: no resume event after child exit", kinds(ev))
    sys.exit(1)
resume = next(e for e in ev if e.get("kind") == "resume")
if resume.get("hub_resume") != "new_hub":
    print("FAIL: hub_resume is not new_hub", resume)
    sys.exit(1)
texts = " ".join(m.get("text", "") for m in resume.get("messages") or [])
if "click the link" not in texts:
    print("FAIL: resume missing transcript", resume)
    sys.exit(1)
send(p, {"cmd": "send", "goal": "again", "profile": "demo"})
ev = read_until(p, lambda v, _c: v.get("kind") == "assistant" and v.get("done") is True, collected=ev)
send(p, {"cmd": "stop"})
try:
    p.stdin.close()
except Exception:
    pass
p.wait(timeout=8)
if not any(e.get("kind") == "assistant" for e in ev):
    print("FAIL: continue after resume produced no assistant")
    sys.exit(1)
print("ok resume after child-exit")
print("PASS jsonl protocol")
PY

echo "desktop crate tests…"
(cd "$ROOT/desktop/src-tauri" && cargo test --offline)

echo "frontend tests…"
(cd "$ROOT/desktop" && npm test)

echo "frontend build…"
(cd "$ROOT/desktop" && npm run build)

echo "PASS desktop M2 smoke"
