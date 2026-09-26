# Windows fonts on Linux (ops) — CloakCLI font gate

CloakCLI spoofs **Windows** Chrome on Linux. Anti-bot font probes expect a
minimum set of **Windows OS** fonts. CloakBrowser warns when they are missing;
CloakCLI turns that into a **gate**:

| Path | Missing fonts |
|---|---|
| **register / strict** (`require_geo` / register skill / `CLOAKCLI_REQUIRE_FONTS=1`) | **fail-closed** — launch aborted |
| **nurture / explore** | **warn only** — logs the missing set, continues |

## Minimum set (detect-only)

Must match `cloakbrowser.browser._WINDOWS_FONT_TELLS` / CloakCLI
`WINDOWS_MINIMUM_FONTS`:

- Segoe UI
- Segoe UI Light
- Calibri
- Marlett
- MS UI Gothic
- Franklin Gothic
- Consolas
- Courier New

Office supplemental fonts (Century Gothic, Wingdings 2, …) are **informational
only** and are **not** part of this gate.

## What CloakCLI will NOT do

- **No silent download or install** of Microsoft-proprietary fonts at runtime.
- **No** `--fingerprint-windows-font-metrics` on Chromium **146** (documented
  effective on **148+** only; emitting it on 146 is a no-op and must not be an
  acceptance claim).
- **No** per-profile fake font-list lottery.

Font presence is checked with `fc-list` only.

## How ops installs a licensed pack

1. Obtain fonts under a **valid Microsoft / OEM / enterprise license** that
   permits install on your Linux fleet image (do **not** scrape Windows ISOs
   or download random “font packs” from the internet without license review).
2. Install into a **fixed, audited** image or package (example layout — adjust
   to your distro and license terms):

   ```bash
   # Example only — paths/package names are site-specific.
   sudo mkdir -p /usr/local/share/fonts/ms-windows-minimum
   # Copy licensed .ttf/.ttc files for the eight families above into that dir.
   sudo fc-cache -f -v
   fc-list | grep -E 'Segoe UI|Calibri|Consolas|Courier New|Marlett|Franklin Gothic|MS UI Gothic'
   ```

3. Bake the pack into the **golden AMI / container / Nix closure** so every
   register host is identical. Record license evidence + package version in
   your change ticket.
4. Verify before opening register:

   ```bash
   PYTHONPATH=python python3 -c "
   from cloakcli_worker.fingerprint import missing_windows_minimum_fonts
   m = missing_windows_minimum_fonts()
   print('missing', m)
   raise SystemExit(0 if m == [] else 1)
   "
   ```

5. Optional env overrides:

   - `CLOAKCLI_REQUIRE_FONTS=1` — force fail-closed even off register paths
   - `CLOAKCLI_REQUIRE_FONTS=0` — force warn-only (not recommended for register)

## Chromium note

Binary under test: **146.0.7680.177.5**. Installing fonts improves
enumeration / fallback / layout consistency with a Windows persona. It does
**not** grant Windows-native font **metrics**; do not claim that until a
148+ binary with `--fingerprint-windows-font-metrics` is proven.
