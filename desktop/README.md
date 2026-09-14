# CloakCLI desktop shell

Tauri 2 **geek ops console** (M3). The Web UI is the product home: Teach Chat, Profiles, Skills, Runs/History, Settings, and a collapsible inspector. **xterm.js + PTY is Diagnostics only** (`cloakcli tui`) and is not started on first paint.

The desktop crate does **not** reimplement CLI, TUI, worker, Playwright, or LLM logic. It:

- draws custom chrome (drag / minimize / maximize / close)
- renders a dense monospace ops UI (`#0b0d10`, 1px separators, cyan/coral accents)
- calls read-only catalog commands (`list_profiles`, `list_skills`, `ops_status`, `list_runs`, `llm_status`, `teach_resume_hint`) that return structured DTOs from the same on-disk layout as the CLI (`profiles/`, `skills/`, `data/`, `config/`)
- starts **Teach Chat** by spawning the resolved `cloakcli` binary with a **fixed argv** (`teach chat --events [--profile …] [--url …] [--no-browser]`) and speaking JSONL on stdin/stdout
- persists redacted teach/job runs under `data/teach/desktop-runs.json` and surfaces the M2 crash snapshot at `data/teach/events-snapshot.json`
- lazy-loads **xterm.js** on the Diagnostics route and opens a real **PTY** for the fixed command `cloakcli tui`

The frontend cannot spawn arbitrary commands. There is no shell plugin. Proxy userinfo is redacted; cookie **values are never copied** into DTOs, events, or logs. All free-text teach events and run summaries are redacted (token / Authorization / cookie / bearer / api_key / `sk-` / proxy userinfo).

Teach Chat is **live** (not a mock): `teach_chat_start` / `teach_chat_send` / `teach_chat_event` / cancel / confirm / status / reconnect. LLM, hub, and Playwright stay in `cloakcli`.

## Layout

```text
desktop/
├─ src/
│  ├─ main.js / shell.js / store.js / api.js / redact.js / errors.js / help.js
│  ├─ features/         # teach-chat, profiles, skills, runs, diagnostics
│  └─ styles.css        # geek theme
├─ src-tauri/           # independent Tauri 2 crate (not in the root Cargo workspace)
│  ├─ src/catalog.rs    # read-only DTOs (no terminal scraping)
│  ├─ src/runs.rs       # redacted history + resume hint + LLM status
│  └─ src/teach.rs      # JSONL adapter
├─ package.json
└─ README.md
```

Root `Cargo.toml` stays a single CLI crate so Tauri never pollutes `cloakcli`.

## Linux (this is the primary dev target)

System packages (Debian/Ubuntu):

```bash
sudo apt-get install -y \
  pkg-config \
  libwebkit2gtk-4.1-dev \
  libgtk-3-dev \
  librsvg2-dev \
  libssl-dev \
  patchelf \
  build-essential
```

`pkg-config` plus **WebKitGTK 4.1** and **GTK 3** headers are required to compile the Tauri webview. If they are missing, `npm run tauri dev` / `tauri build` will fail with a `webkit2gtk-4.1` / `gtk+-3.0` pkg-config error.

Dev:

```bash
# from the CloakCLI repo root
cargo build                     # produces target/debug/cloakcli
cd desktop
npm install
export CLOAKCLI_BIN="$(pwd)/../target/debug/cloakcli"   # absolute
export CLOAKCLI_HOME="$(cd .. && pwd)"                  # absolute repo root
npm run tauri dev
```

`npm run tauri dev` opens an ~1280×800 undecorated window on **Teach Chat**. It does **not** start a PTY. Click **Start** (or send a goal) to spawn `cloakcli teach chat --events`. Uncheck **browser** when there is no display — hub-only mode still plans turns (`--mock-json` / `CLOAKCLI_TEACH_CHAT_MOCK` or a configured LLM) and reports `worker_not_connected` until the headed worker pairs.

Release package (unsigned `.deb`):

```bash
./scripts/desktop-build.sh
# or:
cd desktop && npm run tauri build
```

Artifact (Linux): `desktop/src-tauri/target/release/bundle/deb/CloakCLI_0.3.0_amd64.deb` (plus the unbundled binary `desktop/src-tauri/target/release/cloakcli-desktop`). The `.deb` is the desktop shell only — it does **not** embed the `cloakcli` sidecar. Set `CLOAKCLI_BIN` or put `cloakcli` on `PATH`. Install example:

```bash
sudo dpkg -i desktop/src-tauri/target/release/bundle/deb/CloakCLI_0.3.0_amd64.deb
# then:
export CLOAKCLI_BIN=/absolute/path/to/cloakcli
export CLOAKCLI_HOME=/absolute/path/to/CloakCLI
cloakcli   # or the desktop binary from the package
```

Headed loop (Mac / a box with a display): leave **browser** checked so CloakBrowser + teach extension start, pair, execute actions, and stream job/tool events back.

Keyboard (`?` overlay; also printed on the status bar):

| Key | Action |
|-----|--------|
| `1` | Teach Chat |
| `2` | Profiles |
| `3` | Skills |
| `4` | Runs / History |
| `5` | Diagnostics (Raw TUI) |
| `[` | Toggle inspector |
| `,` | Settings |
| `?` | Keyboard shortcuts overlay |
| `Esc` | Close overlay / settings |
| `Enter` | Send chat (`Shift+Enter` newline) |

Selection shimmer is **off** by default (Settings checkbox). Diagnostics **Start / Stop / Restart** run the existing PTY path (`cloakcli tui` only).

## Binary resolution

1. `CLOAKCLI_BIN` — trusted local override; **must be an absolute existing executable** after canonicalize. Relative paths are rejected. This is not a signed-identity check.
2. A `cloakcli` binary next to the desktop executable. On macOS also `Contents/Resources`, inside the `.app`, and **one directory above the `.app`** (sibling of the bundle).
3. Dev fallback: `target/debug/cloakcli` or `target/release/cloakcli` found by walking up from the desktop executable.
4. `PATH` — **empty and relative entries are ignored** so a namesake in cwd is never exec'd. Each candidate must `canonicalize` to an executable file; failed canonicalize drops the candidate.

If none of those work, the window shows a clear error. It does not guess a relative command.

## `CLOAKCLI_HOME`

Passed into the child as an environment variable (the CLI already prefers it over cwd).

Accepted values are:

- absolute
- existing directories
- no empty string, no relative path, no `.` / `..` segments

Resolution order:

1. process env `CLOAKCLI_HOME`
2. value saved from the in-app field (`app config dir / home.json`)
3. if the desktop binary lives inside this git checkout, the repo root (`Cargo.toml` + `skills/`)

The PTY cwd is **not** used as the project root.

Child environment is **whitelisted** (PATH, locale, display, a small `CLOAKCLI_*` runtime set, proxy vars). `CLOAKCLI_BIN`, `LD_PRELOAD`, and unrelated secrets are not inherited. Tokens that the TUI itself needs (`CLOAKCLI_MASTER_TOKEN`) are inherited but never written to frontend logs.

## macOS (Mac mini) build notes

1. Install Xcode Command Line Tools (`xcode-select --install`) and Rust (`rustup`, rustc 1.85+ matching `desktop/src-tauri/Cargo.lock`).
2. Install Node 20+ (npm).
3. From the repo root: `cargo build` (or `--release`).
4. From `desktop/`: `npm install && npm run tauri dev`.
5. Point `CLOAKCLI_BIN` at an **absolute** `cloakcli` built on that Mac.
6. Set `CLOAKCLI_HOME` to the absolute CloakCLI checkout (or the data directory you want the TUI to use).

Release (unsigned `.app`):

```bash
cd desktop
export CLOAKCLI_BIN="$(cd .. && pwd)/target/release/cloakcli"
export CLOAKCLI_HOME="$(cd .. && pwd)"
npm run tauri build
```

`npm run tauri build` produces an unsigned `.app` under `src-tauri/target/release/bundle/macos/`. That is enough to confirm the same UI launches. **Signing, notarization, and DMG are out of scope for this phase.**

On macOS an empty application menu (app name only) may still appear — there is no File/Edit/shell menu.

Apple Silicon vs Intel: build on the Mini you will run on (no cross-compile in this phase).

## Capabilities

`src-tauri/capabilities/default.json` grants only:

- window chrome: drag, minimize, maximize/restore, close, is-maximized
- events for PTY I/O
- app commands: `shell_status`, `set_home`, `list_profiles`, `list_skills`, `ops_status`, `list_runs`, `llm_status`, `teach_resume_hint`, `pty_start`, `pty_write`, `pty_resize`, `pty_stop`, `teach_chat_start`, `teach_chat_send`, `teach_chat_cancel`, `teach_chat_confirm`, `teach_chat_status`, `teach_chat_stop`, `job_start`, `job_cancel`
- events: `teach_chat_event` (JSONL v=1 kinds: session/status/user/assistant_delta/assistant/system/tool/job/error/closed/resume), `pty-status` / `pty-exit`

No `shell`, `os`, `fs`, or `opener` plugins. `pty_start` always execs the resolved `cloakcli` binary with the single argument `tui`. Catalog commands read files under `CLOAKCLI_HOME`; they never scrape terminal text.

A generic Tauri PTY plugin (`tauri-plugin-pty` / `spawn(cmd, args)`) was not used because it would expose arbitrary command execution to the frontend.

**DevTools:** enabled for `tauri dev` (debug assertions). The `tauri` crate is built **without** the `devtools` feature, so release/WebView inspector is off.

## Window close / orphans

Natural exit, Stop, spawn/reader/writer failure, window close, and app exit share one reclaim path. The session is not cleared until kill + wait finish.

portable-pty `setsid`s the child, so the child's pid is the process-group id. After Ctrl-C, the shell SIGTERMs that group, waits ~2s, then SIGKILLs the group and `wait`s the direct child (no zombie).

cloakcli's Python worker is spawned without `setsid`, so it stays in the group. Processes that leave the group (CloakBrowser/Chrome often daemonize) are reclaimed from a descendant snapshot plus `CLOAKCLI_HOME/data/worker.pid` when that pid appeared after this session started. A daemon that was already running before the window opened is left alone.

## Unicode

PTY reads are decoded with a stateful UTF-8 buffer so a CJK or emoji scalar split across two reads is not turned into U+FFFD. Covered by `drain_utf8` unit tests (`你好`, `😀` split mid-character).

## Crash / reconnect

M2 writes `data/teach/events-snapshot.json` (messages, profile, last request id, page brief). Chat shows a **Resume available** banner when that file exists and no session is running. **Reconnect** / **Start** spawns a new `cloakcli teach chat --events` child, which emits `kind=resume` with `hub_resume: "new_hub"`. The transcript is restored; the hub port is **not** reused. Re-pair the extension for new browser actions.

## Settings

- `CLOAKCLI_HOME` (absolute existing directory)
- Selection shimmer (default **off**)
- LLM status **read-only** from `config/llm.json` (enabled/model/base URL/env name/key present/teach optimize). API key **values** are never copied into the DTO.

## Known limits (this phase)

- Teach Chat is live JSONL to `cloakcli`; Profiles/Skills catalogs stay read-only except selection (profile + optional skill are passed into the turn context).
- Assistant text is streamed as `assistant_delta` chunks (live LLM: HTTP SSE; mock: chunked mock JSON) then a final `assistant` (`done: true`). Stop/cancel is honored between chunks.
- **Hub port is not reused on reconnect.** Each `cloakcli teach chat --events` child binds a new ephemeral teach-hub port and new pairing codes.
- Fleet `master submit` job start is **not wired** in the desktop (`job_start` returns an honest stub). The job card is the in-flight teach turn (start/progress/cancel via teach-chat). History merges those turns with `data/jobs` stubs.
- Top-bar hub/worker lamps are a file + unix-socket connect probe of the **master** control socket / `worker.pid`. Teach hub/worker/extension lamps live on the Chat pair strip.
- Raw TUI still exists on Diagnostics; ratatui is not the product home.
- Linux `.deb` is unsigned and does not bundle the `cloakcli` sidecar. macOS `.app` is unsigned; no notarization/DMG. No Windows installer.
- Linux needs WebKitGTK 4.1 + GTK 3 **dev** packages to compile.
- One PTY session per window; no tabs.
- xterm.js is not a perfect match for every terminal query the TUI might make; report glitches against Diagnostics, not against `cloakcli tui` itself.

## Future packaging

- macOS: signed/notarized `.app` + DMG
- Linux: AppImage / `.deb` with the `cloakcli` sidecar
- Windows: MSI/NSIS + `cloakcli.exe` sidecar
- Sidecar install so `CLOAKCLI_BIN` is unnecessary for release builds
- Optional first-run picker that only accepts validated absolute `CLOAKCLI_HOME`

## Tests

Root CLI tests are unchanged:

```bash
cargo test --offline
```

Desktop path/env/catalog/runs/resume/LLM unit tests (need the Tauri Linux deps to compile the crate):

```bash
cd desktop/src-tauri && cargo test --offline
```

Catalog tests assert proxy userinfo is redacted, cookie values never appear in serialized DTOs, and free-text fields (`notes`, `description`, `on_stall`) redact token / Authorization / cookie samples. Runs tests assert summaries never persist extracts or secret values. Resume-hint tests assert message bodies never leave the snapshot file.

Frontend redaction (pasted secrets never stored/echoed; event payloads redacted; error boundaries; shortcuts):

```bash
cd desktop && npm test
```

JSONL teach-chat smoke (no display, mock model; covers deltas, cancel, child-exit resume):

```bash
./scripts/desktop-m2-smoke.sh
./scripts/desktop-m3-smoke.sh
```

Headed Teach Chat → browser click → result echo (needs display + CloakBrowser):

```bash
./scripts/desktop-m2-headed-smoke.sh
```

CLI events protocol (same as the desktop child):

```bash
cloakcli teach chat --profile demo --events --no-browser --mock-json '{"schema_version":1,"actions":[{"action":"click","selector":"a"},{"action":"done","reason":"ok"}]}'
# stdin: {"cmd":"send","goal":"click the link","profile":"demo"}
#         {"cmd":"stop"}
```

`desktop/src-tauri/Cargo.lock` is pinned so **rustc 1.85** can compile Tauri 2 (newer transitive crates want 1.88). Do not blindly `cargo update` on that crate without checking `rustc --version`.
