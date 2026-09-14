# CloakCLI desktop shell

Tauri 2 **geek ops console** (M1). The Web UI is the product home: Teach Chat, Profiles, Skills, Runs, and a collapsible inspector. **xterm.js + PTY is Diagnostics only** (`cloakcli tui`) and is not started on first paint.

The desktop crate does **not** reimplement CLI, TUI, worker, Playwright, or LLM logic. It:

- draws custom chrome (drag / minimize / maximize / close)
- renders a dense monospace ops UI (`#0b0d10`, 1px separators, cyan/coral accents)
- calls read-only Tauri commands (`list_profiles`, `list_skills`, `ops_status`) that return structured DTOs from the same on-disk layout as the CLI (`profiles/`, `skills/`, `data/`)
- lazy-loads **xterm.js** on the Diagnostics route and opens a real **PTY** for the fixed command `cloakcli tui`

The frontend cannot spawn arbitrary commands. There is no shell plugin. Proxy userinfo is redacted; cookie **values are never copied** into DTOs, events, or logs.

Teach Chat in M1 is a **local protocol mock** (`teach_chat_send` / `teach_chat_event`). Live hub streaming is M2.

## Layout

```text
desktop/
├─ src/
│  ├─ main.js / shell.js / store.js / api.js
│  ├─ features/         # teach-chat, profiles, skills, runs, diagnostics
│  └─ styles.css        # M1 theme
├─ src-tauri/           # independent Tauri 2 crate (not in the root Cargo workspace)
│  └─ src/catalog.rs    # read-only DTOs (no terminal scraping)
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

`pkg-config` plus **WebKitGTK 4.1** and **GTK 3** headers are required to compile the Tauri webview. If they are missing, `npm run tauri dev` / `tauri build` will fail with a `webkit2gtk-4.1` / `gtk+-3.0` pkg-config error. The Rust/JS skeleton in this directory is still the intended app; install the packages and retry.

Run:

```bash
# from the CloakCLI repo root
cargo build                     # produces target/debug/cloakcli
cd desktop
npm install
export CLOAKCLI_BIN="$(pwd)/../target/debug/cloakcli"   # absolute
export CLOAKCLI_HOME="$(cd .. && pwd)"                  # absolute repo root
npm run tauri dev
```

`npm run tauri dev` opens an ~1280×800 undecorated window on **Teach Chat**. It does **not** start a PTY. Profiles / skills / hub+worker status come from the catalog commands.

Keyboard (also printed on the status bar):

| Key | Action |
|-----|--------|
| `1` | Teach Chat |
| `2` | Profiles |
| `3` | Skills |
| `4` | Runs / History |
| `5` | Diagnostics (Raw TUI) |
| `[` | Toggle inspector |
| `,` | Settings stub |

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

1. Install Xcode Command Line Tools and Rust (`rustup`).
2. Install Node 20+ (npm).
3. From this `desktop/` directory: `npm install && npm run tauri dev`.
4. Point `CLOAKCLI_BIN` at an absolute `cloakcli` built with `cargo build` / `cargo build --release` on that Mac.
5. Set `CLOAKCLI_HOME` to the absolute CloakCLI checkout (or the data directory you want the TUI to use).

`npm run tauri build` produces an unsigned `.app` under `src-tauri/target/release/bundle/macos/`. That is enough to confirm the same TUI launches. **Signing, notarization, and DMG are out of scope for this phase.**

On macOS an empty application menu (app name only) may still appear — there is no File/Edit/shell menu.

## Capabilities

`src-tauri/capabilities/default.json` grants only:

- window chrome: drag, minimize, maximize/restore, close, is-maximized
- events for PTY I/O
- app commands: `shell_status`, `set_home`, `list_profiles`, `list_skills`, `ops_status`, `pty_start`, `pty_write`, `pty_resize`, `pty_stop`
- `pty-status` is emitted when stop/reclaim finishes so Diagnostics Start/Stop/Restart can update

No `shell`, `os`, `fs`, or `opener` plugins. `pty_start` always execs the resolved `cloakcli` binary with the single argument `tui`. Catalog commands read files under `CLOAKCLI_HOME`; they never scrape terminal text.

A generic Tauri PTY plugin (`tauri-plugin-pty` / `spawn(cmd, args)`) was not used because it would expose arbitrary command execution to the frontend.

## Window close / orphans

Natural exit, Stop, spawn/reader/writer failure, window close, and app exit share one reclaim path. The session is not cleared until kill + wait finish.

portable-pty `setsid`s the child, so the child's pid is the process-group id. After Ctrl-C, the shell SIGTERMs that group, waits ~2s, then SIGKILLs the group and `wait`s the direct child (no zombie).

cloakcli's Python worker is spawned without `setsid`, so it stays in the group. Processes that leave the group (CloakBrowser/Chrome often daemonize) are reclaimed from a descendant snapshot plus `CLOAKCLI_HOME/data/worker.pid` when that pid appeared after this session started. A daemon that was already running before the window opened is left alone.

## Unicode

PTY reads are decoded with a stateful UTF-8 buffer so a CJK or emoji scalar split across two reads is not turned into U+FFFD. Covered by `drain_utf8` unit tests (`你好`, `😀` split mid-character).

## Known limits (this phase)

- M1: Teach Chat is a static/mock protocol; Profiles/Skills/Runs are read-only.
- Hub status is an honest file probe (control socket / `master.json`), not a live token-authenticated connection.
- Raw TUI still exists on Diagnostics; ratatui is not the product home.
- No formal packaging, code signing, notarization, DMG, AppImage, or Windows installer.
- Linux needs WebKitGTK 4.1 + GTK 3 **dev** packages to compile.
- `tauri build` skeleton is present (`bundle.active`, icons) but unsigned.
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
cargo test
```

Desktop path/env/catalog unit tests (need the Tauri Linux deps to compile the crate):

```bash
cd desktop/src-tauri && cargo test
```

Catalog tests assert proxy userinfo is redacted and cookie values never appear in serialized DTOs.

`desktop/src-tauri/Cargo.lock` is pinned so **rustc 1.85** can compile Tauri 2 (newer transitive crates want 1.88). Do not blindly `cargo update` on that crate without checking `rustc --version`.
