#!/usr/bin/env python3
"""P0 probe: ICE host==exit IP, font gate status, headed window-size flags.

Default mode is offline (no browser): prints font missing set, sample chrome
args (incl. --window-size when headed), and ICE assert self-check vectors.

With --launch: headless CloakBrowser gathers ICE on page/iframe/worker against
a synthetic --fingerprint-webrtc-ip (no real proxy). Use --exit-ip to override.
With --headed: also emit window-size and measure screen/outer/inner when a
display is available.

Never prints proxy URLs or secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from cloakcli_worker.fingerprint import (  # noqa: E402
    WINDOWS_MINIMUM_FONTS,
    WebrtcIceLeakError,
    assert_webrtc_host_equals_exit_ip,
    fingerprint_chrome_args,
    mint_fingerprint_persona,
    missing_windows_minimum_fonts,
    persona_window_geometry,
    verify_webrtc_ice_no_leak,
)


SEED = 42424


def offline_report(exit_ip: str) -> dict:
    persona = mint_fingerprint_persona(SEED)
    missing = missing_windows_minimum_fonts()
    headed_args = fingerprint_chrome_args(
        SEED,
        persona,
        geo={"timezone": "America/New_York", "exit_ip": exit_ip, "locale_from_geo": "en-US"},
        language="en-US",
        headed=True,
    )
    headless_args = fingerprint_chrome_args(
        SEED,
        persona,
        geo={"timezone": "America/New_York", "exit_ip": exit_ip, "locale_from_geo": "en-US"},
        language="en-US",
        headed=False,
    )
    # Synthetic ICE vectors (CI unit-style)
    good = [f"candidate:1 1 UDP 2122260223 {exit_ip} 54400 typ host generation 0"]
    leak = [
        "candidate:1 1 UDP 2122260223 192.168.0.10 54400 typ host generation 0",
        f"candidate:2 1 UDP 2122260223 {exit_ip} 54401 typ host generation 0",
    ]
    ice_ok = True
    ice_err = None
    try:
        assert_webrtc_host_equals_exit_ip(good, exit_ip)
        try:
            assert_webrtc_host_equals_exit_ip(leak, exit_ip)
            ice_ok = False
            ice_err = "leak vector did not fail"
        except WebrtcIceLeakError:
            pass
    except WebrtcIceLeakError as e:
        ice_ok = False
        ice_err = str(e)

    return {
        "mode": "offline",
        "seed": SEED,
        "binary_hint": "146.0.7680.177.5",
        "exit_ip": exit_ip,
        "fonts": {
            "minimum": list(WINDOWS_MINIMUM_FONTS),
            "missing": missing,
            "gate_would_fail_closed": bool(missing),
        },
        "persona_geometry": persona_window_geometry(persona),
        "headed_args": headed_args,
        "headless_args": headless_args,
        "has_window_size": any(a.startswith("--window-size=") for a in headed_args),
        "has_font_metrics_flag": any(
            "fingerprint-windows-font-metrics" in a for a in headed_args + headless_args
        ),
        "ice_vector_selfcheck_ok": ice_ok,
        "ice_vector_error": ice_err,
    }


def live_ice(exit_ip: str, headed: bool) -> dict:
    from cloakbrowser import launch_persistent_context
    from cloakcli_worker.fingerprint import apply_to_launch_kwargs

    persona = mint_fingerprint_persona(SEED)
    ud = ROOT / "data" / "artifacts" / f"fp-ice-probe-{SEED}"
    ud.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {
        "user_data_dir": str(ud),
        "headless": not headed,
    }
    # Full font listing inject so probe can run on boxes without MS fonts.
    listing = "\n".join(f"/x/{f}.ttf: {f}" for f in WINDOWS_MINIMUM_FONTS).lower()
    apply_to_launch_kwargs(
        kwargs,
        seed=SEED,
        headed=headed,
        require_geo=False,
        require_fonts=False,
        font_listing=listing,
    )
    # Force webrtc IP even without proxy geo.
    args = list(kwargs.get("args") or [])
    args = [a for a in args if not a.startswith("--fingerprint-webrtc-ip=")]
    args.append(f"--fingerprint-webrtc-ip={exit_ip}")
    kwargs["args"] = args

    ctx = launch_persistent_context(**kwargs)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("about:blank")
        ice = {}
        ice_error = None
        try:
            ice = verify_webrtc_ice_no_leak(page, exit_ip)
        except WebrtcIceLeakError as e:
            ice_error = str(e)
        measured = page.evaluate(
            """() => ({
              screen: {width: screen.width, height: screen.height,
                       availWidth: screen.availWidth, availHeight: screen.availHeight},
              outer: {width: window.outerWidth, height: window.outerHeight},
              inner: {width: window.innerWidth, height: window.innerHeight},
              dpr: window.devicePixelRatio
            })"""
        )
        return {
            "mode": "launch",
            "headed": headed,
            "seed": SEED,
            "exit_ip": exit_ip,
            "persona": {
                "screen": f"{persona['screen_width']}x{persona['screen_height']}",
                "viewport": f"{persona['viewport_width']}x{persona['viewport_height']}",
            },
            "args": kwargs.get("args"),
            "measured": measured,
            "ice_contexts_ok": ice_error is None,
            "ice_error": ice_error,
            "ice_candidate_counts": {k: len(v) for k, v in ice.items()},
        }
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--exit-ip", default="8.8.8.8")
    args = ap.parse_args()
    if args.launch:
        report = live_ice(args.exit_ip, headed=args.headed)
    else:
        report = offline_report(args.exit_ip)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report.get("ice_vector_selfcheck_ok", True):
        return 2
    if report.get("has_font_metrics_flag"):
        return 3
    if args.launch and not report.get("ice_contexts_ok", True):
        # Live ICE against synthetic IP often fails without real webrtc spoof path
        # matching network; still exit non-zero so CI can gate when configured.
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
