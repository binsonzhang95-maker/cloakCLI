# Fixes for Astra review (+ fleet direction)

**Do not sync to Mac until this checklist is accepted.**  
Sources: `astra-code-review.md`, `astra-fleet-advice.md`, `astra-rereview.md`.  
Response to latest re-review: `astra-rereview-response.md`.

## Product shape (corrected)

- **Master TUI** (typically Mac): configure, schedule, observe.
- **Many remote clients** (cloud boxes): execute jobs / browsers.
- Local multi-browser is **client-internal** only (per-node worker daemon).
- Clients use **outbound** long-lived connections to the master (NAT-friendly). No HA/relay/RBAC in this pass.

## Must-fix from code review — done

| # | Issue | Fix |
|---|--------|-----|
| 1 | Browser session lifecycle (oneshot killed sessions) | Persistent Python daemon on `data/worker.sock`; `cloakcli worker serve/status/stop`; `browser open/list/close` talk to the same daemon across CLI processes |
| 2 | IPC hangs on id mismatch | Request timeouts (`CLOAKCLI_IPC_TIMEOUT`, default 120s); skip mismatched ids; detect closed socket |
| 3 | Unreliable shutdown | Graceful `shutdown` + wait, then kill; TUI leaves daemon/hub running (explicit `worker stop`) |
| 4 | Profile concurrency | Per-profile mkdir lock under `data/locks/<profile>.lock` |
| 5 | Fixed `batch_tmp.json` | Unique `data/batch_<uuid>.json` per run |
| 6 | Path traversal (skill import / names) | Strict `validate_name`; canonicalize + under-root checks |
| 7 | Worker path trust | Daemon sets `CLOAKCLI_ROOT` at start; Python `paths.ensure_under_root` |
| 8 | Proxy credential leak | `redact_proxy` in profile list/show/TUI |
| 9 | Skill params / `{{var}}` | Required params validated; undefined `{{var}}` errors |
| 10 | TUI Sessions honesty | Sessions pane lists **real** daemon sessions; not completed skills |

## Also done (quick)

- Invalid skill JSON reported (`skill list` / doctor / TUI logs)
- Stealth ≠ anonymity note in about/doctor/README
- `--headless` / `--headed` symmetry on batch + skill/browser
- Profile edit (`profile edit`) + TUI create/edit proxy
- Compile break in `master_hub.rs` (`+ "\n"`) fixed; `cargo build` OK

## Fleet = **DEV STUB** (not production)

> **Plaintext TCP JSONL + a single shared master token is for teach/dev only.**  
> It is **not** a production security model. Do not expose the hub to untrusted networks.

| Capability | Stub status |
|------------|-------------|
| Master hub + outbound client dial | Works (dev) |
| `hello` / heartbeat / `job_submit` / `job_state` | Works |
| `config_update` + `config_ack` (diff) + **observed revision** on heartbeat | Works; desired persisted in `data/hub_desired.json` |
| Job persistence under `data/jobs/` + idempotent same-`job_id` | Works (dev) |
| `job_cancel` kills local oneshot worker process | Works (best-effort SIGTERM/KILL) |
| TLS / WebSocket | **TODO before production** |
| Per-client identity, rotation, revoke | **TODO** (still shared token) |
| `skill_sync` tar + SHA-256 / version lock / ACK / rollback | **Works (dev)** — safe extract, immutable digest cache; rollback = sync an older published digest (in-flight jobs keep the digest they started with). TLS still TODO. |

Layers:

1. **Rust master** — TUI embeds hub; `cloakcli master serve`; control unix socket `data/master_ctrl.sock`
2. **Rust client daemon** — `cloakcli client connect --master host:port --id box1`
3. **Python CloakBrowser worker** — local unix-socket daemon / oneshot for browser + skills

### Idempotent recovery note

- Jobs are written to `data/jobs/<job_id>.json` on master and client.
- Re-submitting an already **terminal** `job_id` (`succeeded`/`failed`/`cancelled`) is a no-op (idempotent skip).
- Client reconnect does **not** auto-replay in-flight work; operator may resubmit with a new id, or the same id only if prior run finished.
- Hub **desired** config survives restart via `data/hub_desired.json`.

## Verify locally

```bash
cargo build
./target/debug/cloakcli doctor

# Per-node worker
./target/debug/cloakcli worker serve
./target/debug/cloakcli browser open noproxy --headless
./target/debug/cloakcli browser list
./target/debug/cloakcli browser close all

# Fleet DEV STUB (or run scripts/e2e-fleet-stub.sh)
./target/debug/cloakcli master serve --bind 127.0.0.1:7750 --token dev-token &
./target/debug/cloakcli client connect --master 127.0.0.1:7750 --id box1 --token dev-token &
./target/debug/cloakcli master clients
./target/debug/cloakcli master config --concurrency 3 --headless
./target/debug/cloakcli master skill-pack --skill hello
./target/debug/cloakcli master skill-sync --client box1 --skill hello
./target/debug/cloakcli master submit --client box1 --skill hello --profile noproxy --headless
./target/debug/cloakcli master job-state --job-id <id>
# optional: ./target/debug/cloakcli master cancel --client box1 --job-id <id>
```

Automated smoke: `scripts/e2e-fleet-stub.sh`.

## Remaining / next (production blockers — explicit)

- TLS WebSocket transport; pairing codes → long-lived **per-client** identity + rotate/revoke
- Full secret-manager UI; account lease mutex beyond per-profile lock
- Move Pinterest IMAP runners into package `scripts/` (python_runner contract is in `skills/examples/echo-runner/`)
- Screenshot streaming polish; log resume/checkpoints
- Replace legacy HTTP `client serve` stub (deferred for rustc 1.85 deps)
- **Never** treat plaintext shared `CLOAKCLI_MASTER_TOKEN` as production auth
