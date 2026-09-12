# TUI polish (ops console)

Visual / UX pass on the master TUI so it reads as a **control plane**, not a bare list demo.

## Files

- `src/tui/mod.rs` — app state, keybindings, actions, ~2s live refresh, animation tick, busy waits
- `src/tui/ui.rs` — dark theme rendering (header / tabs / split / footer / status / modal) + lightweight shimmer/throbber

## Layout

1. **Header** (double dim-orange border): `CloakCLI` brand (shimmer) + mid-orange `vX.Y.Z` + purple **DEV STUB** + amber/muted headed pill + `conc:N` + hub bind. Three-level warm-orange: brand/active brightest, version/status mid, border/separator dim.
2. **Tab bar**: Profiles | Skills | Sessions | Clients | Config | Logs — active tab coral-orange inverted; numbered `1`–`6`
3. **Main**: list **60%** + detail **40%** (Config/Logs full-width)
4. **Footer**: context-sensitive key help for the current tab (not a wall of text)
5. **Status line**: last action / errors with green / amber / red tints (not flat inverted bars)
6. **Input modal**: centered double-border dialog for new profile, edit proxy, cookie import/export path

## Detail panel (selected item)

| Pane | Shows |
|------|--------|
| Profiles | name, **redacted** proxy, cookie **status** chip, notes, user_data, created |
| Skills | description, schema, steps/params counts, path |
| Sessions | id, profile, headed, url |
| Clients | ONLINE/offline, last_seen, obs rev, last job summary |

## Visual style

Claude Code–inspired **rich multi-color dark theme** (`Color::Rgb`, not flat cyan-on-black):

| Token | RGB | Use |
|-------|-----|-----|
| BG | 22,22,24 | canvas |
| Surface | 30,30,30 | modal / raised panels |
| FG | 230,230,230 | primary text, names |
| Muted | 120,120,128 | labels, inactive tabs, inactive borders |
| Accent (coral-orange) | 217,119,87 | title, focus border, selected tab/row, footer keys, modal |
| Info blue | 96,165,250 | hub address, paths, redacted proxy chips |
| Purple | 167,139,250 | DEV STUB, cookie status chips |
| OK green | 74,222,128 | ONLINE, ready/ok status |
| Warn amber | 251,191,36 | HEADED, warnings |
| Err red | 248,113,113 | errors |

- `Block::bordered` + `BorderType::Rounded` (header/modal: `Double`)
- List pane border orange (focused); detail pane muted
- Selected row: orange reverse + `▸`
- ONLINE clients green; offline dim
- Cookie chips purple (status only); proxy chips info blue (already redacted)
- Footer: key letters orange, descriptions muted

## UX

- Auto-refresh sessions + clients (~2s) while idle / not in a modal
- `j`/`k` + arrows; `Tab` / `Shift-Tab` / `1`–`6`
- Kept: `n`/`e`/`o`/`x`, cookie `i`/`E`/`C`, Enter run, `J` remote job, `h` headed, `c`/`[`/`]` concurrency, `q` quit
- Empty states with one-line hints
- Logs: chronological with timestamps, viewport shows latest at bottom; color by OK/FAIL
- Cookie import/export modals now **commit** (previously only new-profile / edit-proxy did)

## Safety (unchanged)

- No cookie values in UI/logs — counts/domains via `cookies::status_summary`
- Proxy via `redact_proxy` / `profiles::display_proxy` only

## Build

- Target: rustc 1.85 / `ratatui = 0.28.1` APIs only (`Block::bordered`, `BorderType::{Rounded,Double}`, `Clear`, `title_bottom`)
- `cargo build` OK; CLI subcommands untouched

## Animation (MVP)

Hand-rolled shimmer (~50 lines in `ui.rs`) — **not** `tui-shimmer`, **no** ratatui upgrade. Sweeps a 2-cell brightness/bold highlight across:

- `CloakCLI` brand
- current pane title (active tab + focused list/config/logs title)
- short busy status text

ASCII throbber (`| / - \`) via `throbber-widgets-tui = 0.7.1` (verified single `ratatui 0.28.1`). Shown **only** during real waits: worker start/refresh, skill run, hub connect, sessions load. Idle, input modal, and error states stay static.

- Tick: existing ~100ms event-loop poll (`animation_phase` + throbber step)
- Off: `NO_COLOR` (any value) or `CLOAKCLI_ANIMATIONS=0/false/off` — static titles remain; busy still prints `[busy] starting worker` (etc.)
- Animation is never the only signal — status text always says what is waiting

## README

- TUI keys section updated for new chrome + bindings
