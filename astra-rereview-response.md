# Response to `astra-rereview.md` (conditional-pass ask)

**Ask:** Allow Mac `~/Documents/CloakCLI` sync for continued teach/dev — **not** claiming production-ready fleet.

## Immediate blockers

| Item | Status |
|------|--------|
| `master_hub.rs` literal newline string concat (`+ "\n"`) | **Fixed** (verified `cargo build`) |
| `cargo build` | **OK** (unused-code warnings only) |
| Mark fleet as **dev stub** in README + FIXES | **Done** — plaintext shared token ≠ production; TLS/WS, per-client identity, skill_sync hashes listed as TODO |

## Conditional-pass items

| Item | What we did | Verified on this box |
|------|-------------|----------------------|
| Minimal e2e / verified commands | `scripts/e2e-fleet-stub.sh` + README/FIXES commands | **E2E OK** — master serve → client connect → clients → config → submit hello → job_state `succeeded` (title Example Domain) → idempotent resubmit |
| Real `config_update` ack/diff + heartbeat **observed** revision | `master config` persists `data/hub_desired.json`, pushes `config_update`; client applies + `config_ack` with **diff**; heartbeats/acks report real `observed` | After push: `observed_revision: 4` matching desired; log shows diff ack |
| Job persistence / idempotent recovery + cancel stops work | `data/jobs/<id>.json`; terminal same-`job_id` no-op; `job_cancel` → kill flag + `oneshot_killable` SIGTERM/KILL | Persist + idempotent OK in e2e; cancel CLI path OK (hello finishes too fast to race-kill; mechanism wired) |
| Persist hub desired across restart | load/save `data/hub_desired.json` | File present after `master config` |

## Docs

- `README.md` — fleet **DEV STUB** honesty + commands including `config` / `job-state` / e2e script
- `FIXES-FOR-ASTRA.md` — stub table, production TODOs, idempotent recovery note, verify block

## Explicit non-claims (still stub)

- Not TLS/WebSocket
- Not per-client pairing identity / rotate / revoke (still shared plaintext token)
- `skill_sync` still stub (ack “not implemented”)
- **Not production-ready** fleet security

## Verify commands

```bash
cargo build
./target/debug/cloakcli doctor
./scripts/e2e-fleet-stub.sh
```

**Goal for Astra:** conditional pass to sync Mac Documents for teach/dev only.
