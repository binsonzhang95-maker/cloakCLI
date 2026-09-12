# Cookie MVP — what shipped vs Astra plan / code review

Reference: `astra-cookie-plan.md`, `astra-cookie-code-review.md`.

## Shipped

### Storage
- Layout: `profiles/<name>/profile.json` + `profiles/<name>/cookie.json` (mode `0600`).
- `profile.json` is metadata (typically `0644`) — **not** a secret; only `cookie.json` and export files are secrets.
- `cookie.json` = Playwright storage_state: `{"cookies":[...],"origins":[...]}` (`origins` optional/empty).
- **Separate from** `user_data_dir` (`data/profiles/<name>/`).
- Atomic write (temp + rename) + `0600` (re-applied after rename on existing targets).
- `.gitignore`: `**/cookie.json`, export patterns (`**/cookies-export*.json`, `**/storage_state*.json`, `*.cookies.json`).

### Migration choice
- **Read** both legacy flat `profiles/<name>.json` and new `profiles/<name>/profile.json`.
- **Migrate on write / cookie ops / open**: `ensure_dir_layout` runs on profile create/update, **all** cookie ops (`import` / `export` / `clear` / `status`), and before open (`cookie_file_for_open`). Flat file is renamed into `profiles/<name>/profile.json`.
- Not eager on every `list`/`get` read (avoids surprise mass moves).

### Import formats
- `--format auto|storage-state|cookies-json`
- `cookies-json`: bare `[...]` or `{cookies:[...]}`
- Netscape: **not** implemented this pass (optional / phase 2).

### CLI
```
cloakcli profile cookie import <profile> <file> [--format auto|storage-state|cookies-json]
cloakcli profile cookie export <profile> [--out FILE]
cloakcli profile cookie clear <profile> [--close-sessions]
cloakcli profile cookie status <profile>
```
- Export default: stdout.
- **`--out` policy** (tightened after Astra review):
  - Writes file mode `0600` (including when overwriting an existing file).
  - Refuses **directory** targets.
  - Refuses writing into project `data/` or `artifacts/` (public/runtime dirs).
  - Allowed: absolute paths outside those dirs, or relative paths that do not resolve under them (e.g. `/tmp/p-cookies.json`, `./my-export.json` at repo root).
- Status JSON: `present`, `cookie_count`, `valid_count`, `expired_count`, `origin_count`, `domains`, `path` — **never values**.
- Status/list/TUI: counts + domains only — **never values** (regression covered in unit + verify script).

### Worker / open path
- After `launch_persistent_context`, if `cookie_file` set (under project root), worker re-validates full cookie schema then `context.add_cookies(...)`.
- Stable `INVALID_COOKIE` errors for bad schema / unreadable / non-object entries (worker never trusts file blindly).
- **Origins / localStorage injection: disabled by default.** Imported `origins` are stored for format compatibility but not applied (no arbitrary `page.goto`). Opt-in: `CLOAKCLI_APPLY_ORIGINS=1` enables strict http(s) allowlist only (blocks localhost / private / link-local / non-http(s)).
- Rust `cookie_file_for_open`:
  - Calls `ensure_dir_layout`.
  - Canonicalizes path; requires regular file named `cookie.json` under that profile dir; **rejects symlinks**.
  - If file present but corrupt/unreadable/invalid schema → **open fails clearly** (`INVALID_COOKIE`), does not silent-skip.
  - If file absent → `None` (open without cookies).
- Passed on `browser open`, `skill run`, batch, TUI open/run, client daemon jobs.
- Existing sessions **not** hot-updated; `clear --close-sessions` closes matching profile sessions.
- JSONL responses may include `{cookies_applied, domains, ...}` metadata only.

### TUI
- Profiles pane: `[cookies=none|N cookies [domains]|N valid/M exp [...]]`
- Keys: `i` import path, `E` export path, `C` clear (no per-cookie editor).

### Tests / verify
- Unit tests in `src/cookies.rs` (parse, 0600 new+existing, roundtrip, flat→dir on status/import, export policy, symlink reject, corrupt open, expired_count, value non-leak).
- Script: `scripts/verify-cookies.sh`
- Fixture: `fixtures/sample-cookies.json`

## Changes vs Astra cookie code review (`astra-cookie-code-review.md`)

| Astra must-fix | Status |
|-----------------|--------|
| `ensure_dir_layout` on all cookie ops + before open; docs aligned | Done |
| Worker `apply_cookie_file` full schema validation + stable `INVALID_COOKIE` | Done |
| Disable origins injection by default (or strict allowlist); document | Done (off by default; opt-in strict allowlist) |
| Rust `cookie_file_for_open` canonicalize; regular file under profile dir; reject symlinks | Done |
| `export --out` refuse `data/` / directory targets; 0600 on existing; document | Done |
| Tests: 0600 existing targets, symlink rejection | Done |

| Astra should-fix (this pass) | Status |
|-------------------------------|--------|
| status `expired_count` / valid summary | Done (`valid_count` + `expired_count`) |
| open fails clearly if cookie present but corrupt | Done |
| regression: values never in status/logs | Done (unit + verify) |

Still deferred (Astra “可后续” / out of scope): Netscape, keychain, separate JSONL cookie ops, TUI per-cookie editor, full origins merge.

## Out of scope (this pass) — matches Astra
- Encrypted remote cookie push to fleet
- Full Netscape import
- TUI per-cookie editor / expiry cleanup / cross-profile copy
- OS keychain / encrypted-at-rest cookie store
- Worker ops `cookie.validate` / `cookie.apply` as separate JSONL commands (injection is via `cookie_file` on open/run_skill instead)

## Apply semantics
1. `profile cookie import` validates + normalizes → writes `cookie.json`
2. Next `browser open` / `skill run` ensures layout, validates cookie path (no symlink), worker re-validates schema, then injects cookies (origins not applied unless opt-in)
3. Re-import does not update already-open sessions — close + open again
4. Corrupt/unreadable `cookie.json` → open/skill fails with `INVALID_COOKIE` (fix or clear + re-import)
