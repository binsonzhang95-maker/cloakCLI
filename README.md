# CloakCLI

基于 [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) 的 **Rust master TUI/CLI + Rust client daemon + 薄 Python worker**。

> **Stealth ≠ anonymity / anti-detect guarantee.** CloakBrowser reduces automation fingerprints; it does not promise anonymity or bypassing all detection.

## Architecture (master → N clients)

```
Mac (master TUI)                      Cloud box / client node
─────────────────                     ───────────────────────
cloakcli (TUI)                        cloakcli client connect
  · profiles / skills / config           (outbound TCP JSONL)
  · Clients pane / job submit     ←──► master hub :7750
  · observe job_state / logs              │
cloakcli master serve (optional)          ▼
                                     python cloakcli_worker (unix socket)
                                          ▼
                                     CloakBrowser persistent context
```

- **Master**: configure, schedule, observe (TUI primary; CLI for scripting).
- **Client**: execute; dials **out** to master (NAT-friendly). Local multi-browser is client-internal via `worker serve`.
- **Protocol (DEV STUB)**: versioned JSONL over **plaintext TCP** (`hello`, `heartbeat`, `job_submit` / `job_state` / `job_cancel`, `config_update` / `config_ack`, `log_chunk`, …). See `astra-fleet-advice.md`.
- **Security honesty**: shared `CLOAKCLI_MASTER_TOKEN` in cleartext is **dev-only**. **Not production.**
- **TODO before production**: TLS/WebSocket, per-client identity (pair/rotate/revoke), `skill_sync` content hashes, HA/relay/RBAC.

本仓库在 box 上：`/workspace/CloakCLI`（Mac 稍后同步到 `~/Documents/CloakCLI`）。

## Desktop shell (Tauri 2)

Optional **geek ops console** (`desktop/`, M3). Teach Chat is the home page. Profiles / skills / runs / LLM status come from read-only Tauri DTOs. Teach Chat is live JSONL to `cloakcli`. The raw TUI (`cloakcli tui` in a PTY) is **Diagnostics only** and is not started on first paint. See [`desktop/README.md`](desktop/README.md) for Linux + Mac mini run/dev/build.

```bash
cargo build
cd desktop && npm install
export CLOAKCLI_BIN="$(pwd)/../target/debug/cloakcli"
export CLOAKCLI_HOME="$(cd .. && pwd)"
npm run tauri dev
```

Keys: `1–5` switch Teach Chat / Profiles / Skills / Runs / Diagnostics; `[` inspector; `,` settings; `?` shortcuts. Shimmer is off by default.

Linux package: `./scripts/desktop-build.sh` → unsigned `desktop/src-tauri/target/release/bundle/deb/CloakCLI_0.3.0_amd64.deb` (shell only; set `CLOAKCLI_BIN`). Mac mini: same command produces an unsigned `.app` under `bundle/macos/`. Signing / notarization / DMG / sidecar are out of scope.

## Per-node browser daemon

Sessions must survive across CLI invocations:

```bash
cloakcli worker serve          # data/worker.sock + data/worker.pid
cloakcli browser open demo --url https://example.com
cloakcli browser list          # other process — same sessions
cloakcli browser close all
cloakcli worker status
cloakcli worker stop           # close browsers, exit daemon
```

`doctor` prints daemon status. IPC requests time out (`CLOAKCLI_IPC_TIMEOUT`, default 120s).

## Install

```bash
cd ~/Documents/CloakCLI   # or /workspace/CloakCLI
cargo build --release
python3 -m pip install -e python/   # or --user --break-system-packages on PEP 668
cloakcli doctor
```

| Env | Meaning |
|-----|---------|
| `CLOAKCLI_HOME` | Project root override |
| `CLOAKCLI_HEADED=1` | Default headed |
| `CLOAKCLI_PYTHON` | Python binary |
| `CLOAKCLI_IPC_TIMEOUT` | Worker/daemon IPC timeout (seconds); skill runs extend to recover budget + 60s when LLM recover is enabled |
| `CLOAKCLI_MASTER_BIND` | Master listen addr (default `127.0.0.1:7750`) |
| `CLOAKCLI_MASTER_TOKEN` | **Dev stub** shared token (plaintext). NOT production auth |
| `NO_COLOR` | Disable TUI shimmer/throbber (static busy text still shown) |
| `CLOAKCLI_ANIMATIONS=0` | Same as `NO_COLOR` for TUI animation (`false`/`off`/`no` also work) |

## Quick start

```bash
# Master TUI (embeds hub on :7750; Clients pane)
cloakcli
cloakcli tui

# Profiles / skills (local)
cloakcli profile create demo --proxy http://user:pass@127.0.0.1:7890
cloakcli profile list                    # proxy credentials redacted
cloakcli profile edit demo --proxy http://127.0.0.1:7890
cloakcli skill list
cloakcli skill run hello --profile demo --headless

# Batch (unique temp files; per-profile locks)
cloakcli batch run --skill hello --profiles demo,demo2 --concurrency 2 --headless

# Fleet DEV STUB (plaintext shared token ≠ production)
cloakcli master serve --bind 127.0.0.1:7750 --token dev-token
# on each client box:
cloakcli client connect --master 127.0.0.1:7750 --id box1 --token dev-token
# from master side:
cloakcli master clients
cloakcli master config --concurrency 3 --headless   # persists data/hub_desired.json + push
cloakcli master submit --client box1 --skill hello --profile noproxy --headless
cloakcli master job-state --job-id <id>
# smoke: scripts/e2e-fleet-stub.sh
```

### TUI keys

Ops-console layout: **header** (shimmering `CloakCLI` / version / DEV STUB / headed / concurrency / hub) → **tabs** → **list+detail** → **context help** → **status**. Forms open as a centered modal. Lightweight shimmer on the brand, current pane title, and short busy text; ASCII `| / - \` throbber only while starting/refreshing the worker, running a skill, connecting the hub, or loading sessions. `NO_COLOR` or `CLOAKCLI_ANIMATIONS=0` freezes animation but still shows `[busy]` plus the wait reason.

| Key | Action |
|-----|--------|
| `T` | Teach on selected profile (headed CloakBrowser + bundled extension; same as `cloakcli teach start`) |
| `q` / Esc | Quit TUI (daemon/hub keep running) |
| `Tab` / `Shift-Tab` / `1`–`6` | Switch panes: Profiles · Skills · Sessions · Clients · Config · Logs |
| `j`/`k` or ↑↓ | Navigate list |
| `Enter` | Run selected skill on selected profile **locally** |
| `J` | Submit job to **selected remote client** |
| `o` / `x` | Open / close **local** browser session |
| `n` / `e` | New profile / edit proxy (modal) |
| `i` / `E` / `C` | Import / export / clear **cookies** (Profiles pane; modal for paths) |
| `h` | Toggle headed default |
| `l` | Config pane: toggle LLM stall-recover `enabled` |
| `c` or `[` `]` | Concurrency + / − / + |
| `r` | Reload profiles/skills |
| (idle) | Sessions + clients auto-refresh ~2s |
| (busy) | Throbber + status text during worker/hub/skill/sessions waits |

Cookie **values** and full proxy credentials never appear in the TUI (status chips / `redact_proxy` only).


## Cookies + proxy (core)

Cookie files and proxy settings are **first-class profile config**, separate from Chromium `user_data_dir`:

| Path | Purpose |
|------|---------|
| `profiles/<name>/profile.json` | Metadata (name, proxy, notes, user_data_dir) — syncable |
| `profiles/<name>/cookie.json` | Playwright `storage_state` (`cookies` + optional `origins`) — **secret**, mode `0600` |
| `data/profiles/<name>/` | Browser persistent context (runtime) |

**Do not commit `cookie.json` or cookie exports** (see `.gitignore`). Never paste cookie values into logs, screenshots, or job output.

```bash
# Import (auto-detects storage_state or cookies array)
cloakcli profile cookie import noproxy ./fixtures/sample-cookies.json --format auto
cloakcli profile cookie status noproxy          # counts/domains only
cloakcli profile cookie export noproxy --out /tmp/noproxy-cookies.json  # not data/; mode 0600
cloakcli profile cookie clear noproxy --close-sessions

# Cookies apply on *new* open / skill run (not hot-updated into existing sessions)
cloakcli browser open noproxy --url https://example.com --headless
cloakcli skill run hello --profile noproxy --headless
```

Legacy flat `profiles/<name>.json` is still readable; cookie ops (`import`/`export`/`clear`/`status`), profile update/create, and open migrate to `profiles/<name>/profile.json`.

TUI Profiles pane shows cookie status; keys `i` / `E` / `C` = import path / export path / clear.

## Teach (record a skill)

Independent MV3 extension at `extensions/teach/`. Master/fleet **do not teach** — they only consume exported `skills/<name>/skill.json`.

```bash
cloakcli teach start --profile demo --url https://example.com
# in the browser: extension popup → Record → click/type/navigate → Mark goal → Stop → Export
cloakcli skill run <exported-name> --profile demo --var PASSWORD=...
```

- Headed CloakBrowser only (headless is a hard error). The extension path is resolved from the install/repo; there is **no** `--extension` / `--load-extension` CLI inject.
- TUI key `T` calls the same `teach start` path (no second launcher).
- Export mapping: navigation → `goto`, click → `click` (selector), input → `fill`. Skill-level `goal` is the last marked goal; empty steps are rejected; missing goal is allowed (runner fallbacks).
- **Local post-process** (no LLM): merge consecutive fills, drop hover/duplicate clicks, attach backup selector chains (`id` / `name` / `autocomplete` / `data-testid`) and semantic `field_name`s.
- **Smart optimize** (default ON, `--no-smart-optimize` to skip): one LLM call after export returns a structured patch (selectors, goal, merge, field names). Local validation (teach action whitelist, selector syntax, step order) runs before save. Fail-open if the model is down.
- Recording never calls the model per click/fill. Optional mid-record assist is capped at **2** LLM calls when a selector looks unstable, and is deferred when that is not cheap.
- Password / token / secret fields export as `{{vars.NAME}}` by default (listed in the generated README). Explicit `--allow-secrets` keeps plaintext and still writes a `.gitignore`.
- Content script is origin-allowlisted: the CLI `--url` origin plus origins you **Allow** in the extension popup. Navigating to another HTTPS site does not silent-add it. Manifest has no `<all_urls>`.
- Takes the profile lock for the whole session; conflict with a batch worker prints a clear error.

Teach and recover are separate paths: teach writes `skill.json`; recover only runs later if a step stalls.

Fixture round-trip: `fixtures/teach/recorded-events.json` → `fixtures/teach/expected-skill.json`.

### Teach Chat M1 headed smoke

Repeatable headed CloakBrowser acceptance for pairing, `page_state`, allowlist inject, service-worker restart, worker reconnect, duplicate pairing, and log leak scan (planted sentinel token/password/cookie values must not appear as raw substrings; field-name / URL-query / nested JSON checks stay as defense-in-depth):

```bash
./scripts/e2e-teach-m1-smoke.sh
# or:
CLOAKCLI_BIN=target/release/cloakcli python3 scripts/teach_m1_headed_smoke.py
```

Requires a display (`DISPLAY` / `WAYLAND_DISPLAY`) or `xvfb-run`, a built `cloakcli`, and the CloakBrowser Chromium binary. The script starts two loopback origins (allow + deny), runs `cloakcli teach start`, and fails if hub events or worker JSON miss the M1 checks. Non-headed coverage (SW token reuse, worker reconnect, duplicate pairing) lives in `python/tests/test_teach_chat_protocol.py` and `cargo test`.

### Teach Chat M1–M4

`cloakcli teach chat` is the Claude-style teaching shell: TUI dialogue → at most 3 validated Playwright actions → optional human takeover → skill draft export.

```bash
cloakcli teach chat --profile demo --url https://example.com
# in the TUI:
#   Enter     send a goal (LLM plans ≤3 actions)
#   Ctrl-T    start human takeover (REC=on, agent paused)
#             use the browser: click, type, navigate
#   Ctrl-T    stop → local normalize → Playwright steps source=human
#   Y / N     accept or drop unstable/coords/iframe selectors
#   Ctrl-R    resume the agent from the current page + human-step summary
#   Ctrl-E    export a skills/<name>/skill.json draft
#             type a skill name, Enter to write
#             if that name exists: Y overwrite / N cancel (Enter does not overwrite)
```

After a session, Ctrl-E writes `skills/<name>/skill.json` plus README, `.gitignore`, and `AUDIT.md`. Steps are unified Playwright actions (`goto` / `click` / `fill` / …) with `source=human|agent`. Password/token/cookie fields become `{{vars.PASSWORD}}` (etc.); plaintext secrets are never written. Raw DOM events are never skill steps. Export refuses to overwrite an existing skill unless you confirm with Y. Pairing codes expire and cannot be replayed.

Headless export (tests / recovery):

```bash
cloakcli teach export --name taught-login --goal "Sign in" \
  --steps-json '[{"action":"goto","url":"https://example.com/login","source":"agent"}]'
# failed export does not touch an existing skills/<name>/skill.json
cloakcli skill run taught-login --profile demo --var PASSWORD=...
```

If export fails (illegal step, path escape, duplicate name), the previous skill.json is left intact. Start a new session if pairing was consumed or expired.

Password/token fields are stored as type + length + `redacted` only; fills become `{{vars.PASSWORD}}` (or similar). Any `http(s)` goto is recorded even off the teach allowlist; `javascript:` / `file:` / `data:` are rejected. Selectors prefer `id` → `data-testid` → `name` → aria/role → text → CSS path → coords (re-checked unique on the page). Shadow DOM and missing selectors are non-exportable (never silent-saved as raw events).

## Skill format

```json
{
  "schema_version": 1,
  "name": "hello",
  "description": "open example.com",
  "params": [],
  "steps": [
    {"action": "goto", "url": "https://example.com"},
    {"action": "wait", "ms": 500},
    {"action": "extract_text", "css": "h1", "as": "title"},
    {"action": "screenshot", "path": "artifacts/hello.png"}
  ]
}
```

Actions: `goto` `click` `type`/`fill` `wait` `screenshot` `extract_text` `assert`.  
`{{var}}` substitution errors if undefined; required `params` are validated.

Optional stall-recovery fields (inherit skill → step; default `on_stall` is `fail`):

```json
{
  "on_stall": "recover",
  "steps": [
    {
      "action": "click",
      "selector": "#maybe-missing",
      "timeout": 4000,
      "goal": "Click the More information link",
      "on_stall": "recover"
    }
  ]
}
```

On timeout / selector / assertion failure with `on_stall: recover`, the Python worker keeps the **existing** Playwright page and runs a **cascade** (success stops): local selectors / backups → text model + DOM summary (no screenshot) → **one** compressed/crop vision shot (viewport JPEG, never a full-page original). Default recover wall-clock budget is **90 seconds** (`recover_timeout_sec`; form range 60–120). **300 remains an advanced override.** Max **3** model rounds (`max_model_rounds`). Telemetry (tokens, latency, rounds, screenshot bytes, success/fail) is observational — there is no hard min-token goal. No host filesystem read, no shell/code exec. `goto` stays on the task start origin unless `allow_hosts` lists extra hosts; `file:` / `javascript:` / `data:` are rejected.

Recover form whitelist (in-browser only):

| Action | Notes |
|--------|--------|
| `click` | CSS selector, **or** (vision stage) `x`/`y` **plus** `screenshot_id` equal to the current observation. Coordinate clicks without an id, with a stale id, or after navigation/viewport change are rejected. |
| `fill` / `type` | Playwright `page.fill` or click-then-`keyboard.type`. **Kept on purpose** — full in-browser control, not host I/O. |
| `press` | Whitelisted keys: Enter, Tab, Escape, arrows, Space, Backspace, Home, End. |
| `select` | CSS + value on the existing `<select>`. |
| `scroll` | Small only (`|delta|<=800`) or `css` into-view. |
| `wait` `goto` | Policy-limited `goto` (same origin / `allow_hosts`). |
| `done` `fail` `ask_human` | Terminal; `ask_human` pauses the skill (`status=paused`). |

Recover **may** type into username/password form fields when the skill needs login. Trajectories and logs still never persist API keys, Authorization headers, raw `llm.json` secrets, or cookie **values**. Config keys stay env-var-only (`api_key_env`). See [`python/cloakcli_worker/recover/NOTES.md`](python/cloakcli_worker/recover/NOTES.md).

```bash
export CLOAKCLI_LLM_API_KEY=...          # or OPENAI_API_KEY (compat); never --api-key
cloakcli llm configure --base-url https://api.openai.com/v1
# non-interactive: --model gpt-4o   or   --pick 1
# pipe key:        --stdin-key  (do not put the key on the command line)
cloakcli llm models  # GET {base}/models — prints ids only
cloakcli llm set --model gpt-4o   # checked against last fetch when cache exists
cloakcli llm show    # never prints key values
cloakcli llm test    # connectivity; redacts secrets
```

`config/llm.json` is mode `0600` and stores the **env var name** only (`api_key_env`, default `CLOAKCLI_LLM_API_KEY`), never a raw key. `OPENAI_API_KEY` is a documented fallback when the default env is unset. `--api-key` as argv is rejected (shell history). `base_url` is http(s) only, trailing slash stripped, single `/v1` — CloakCLI calls `{base}/models` and `{base}/chat/completions` without duplicating `/v1`.

TUI Config: `b` base_url, `K` masked session key (sets process env / `api_key_env`, **not** saved to disk), `f` fetch models, `jk`/`↑↓` select, Enter save model, `l` toggle enabled, `t` recover timeout. A failed fetch does not change the saved model. Example skill: `skills/examples/recover-demo/`.

## Security notes

- Profile/skill names: strict charset; import `--name` cannot escape `skills/`.
- Worker only accepts paths under project root set at daemon start.
- Proxy URLs redacted in list/show/TUI.
- Cookie **values** never appear in CLI status, TUI, or worker JSONL responses (counts/domains only).
- Worker re-validates cookie schema on apply; origins/localStorage injection is **off by default** (`CLOAKCLI_APPLY_ORIGINS=1` for strict http(s) allowlist).
- `export --out` refuses project `data/` + `artifacts/` and directory targets; always `0600`.
- `**/cookie.json` and cookie export patterns are gitignored — do not sync secrets into git/artifacts.
- **Fleet is a DEV STUB**: plaintext TCP + shared token. TLS/WS, per-client identity, and skill_sync hashes remain **TODO** before any production use.

## Dev

Box uses **rustc 1.85** — keep `ratatui=0.28.1` / pinned `instability`/`darling` in lockfile.

```bash
cargo build
cargo test
PYTHONPATH=python python3 -m unittest discover -s python/tests -v
cloakcli doctor
PYTHONPATH=python python3 -m cloakcli_worker serve --root "$PWD" --socket data/worker.sock
```

See `FIXES-FOR-ASTRA.md` for review mapping.

## License

MIT（业务代码；CloakBrowser 二进制另有其许可）
