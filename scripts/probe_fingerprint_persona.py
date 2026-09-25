#!/usr/bin/env python3
"""Print coherent persona + geo flags for a few seeds.

Does not launch a browser unless --launch is passed. Never prints proxy URLs.
WebRTC ICE follow-up (not run here):
  const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
  pc.createDataChannel('x');
  pc.onicecandidate = e => console.log(e.candidate && e.candidate.candidate);
  pc.createOffer().then(o => pc.setLocalDescription(o));
Host candidates should match --fingerprint-webrtc-ip, not the machine IP.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from cloakcli_worker.fingerprint import (  # noqa: E402
    apply_to_launch_kwargs,
    fingerprint_chrome_args,
    mint_fingerprint_persona,
)


SEEDS = (11111, 42424, 99887)


def probe_flags() -> list[dict]:
    rows = []
    for seed in SEEDS:
        persona = mint_fingerprint_persona(seed)
        args = fingerprint_chrome_args(
            seed,
            persona,
            geo={
                "timezone": "America/New_York",
                "exit_ip": "8.8.8.8",
                "locale_from_geo": "en-US",
            },
            language="en-US",
        )
        rows.append(
            {
                "seed": seed,
                "brand": persona["brand"],
                "brand_version": persona["brand_version"],
                "chromium_compatible": persona["chromium_compatible"],
                "platform": persona["platform"],
                "platform_version": persona["platform_version"],
                "hardware_concurrency": persona["hardware_concurrency"],
                "device_memory": persona["device_memory"],
                "screen": f"{persona['screen_width']}x{persona['screen_height']}",
                "viewport": f"{persona['viewport_width']}x{persona['viewport_height']}",
                "available": f"{persona['available_width']}x{persona['available_height']}",
                "args": args,
            }
        )
    return rows


def probe_live() -> list[dict]:
    """Headless evaluate UA + high-entropy CH + timezone/lang. No proxy."""
    from cloakbrowser import launch_persistent_context

    out = []
    for seed in SEEDS:
        persona = mint_fingerprint_persona(seed)
        kwargs: dict = {
            "user_data_dir": str(ROOT / "data" / "artifacts" / f"fp-probe-{seed}"),
            "headless": True,
        }
        apply_to_launch_kwargs(
            kwargs,
            seed=seed,
            headed=False,
            require_geo=False,
        )
        ctx = launch_persistent_context(**kwargs)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            info = page.evaluate(
                """() => {
                  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
                  const lang = navigator.language;
                  const langs = navigator.languages;
                  const ua = navigator.userAgent;
                  const d = navigator.userAgentData || null;
                  return (async () => {
                    let high = null;
                    if (d && d.getHighEntropyValues) {
                      high = await d.getHighEntropyValues([
                        'architecture','bitness','model','platformVersion',
                        'uaFullVersion','fullVersionList','wow64'
                      ]);
                    }
                    return {
                      ua,
                      brands: d ? d.brands : null,
                      platform: d ? d.platform : null,
                      mobile: d ? d.mobile : null,
                      high,
                      language: lang,
                      languages: langs,
                      timezone: tz,
                      hardwareConcurrency: navigator.hardwareConcurrency,
                      deviceMemory: navigator.deviceMemory,
                      screen: {width: screen.width, height: screen.height,
                               availWidth: screen.availWidth, availHeight: screen.availHeight}
                    };
                  })();
                }"""
            )
            out.append(
                {
                    "seed": seed,
                    "persona": {
                        "brand": persona["brand"],
                        "brand_version": persona["brand_version"],
                        "platform_version": persona["platform_version"],
                        "hardware_concurrency": persona["hardware_concurrency"],
                        "device_memory": persona["device_memory"],
                    },
                    "observed": info,
                    "args": kwargs.get("args"),
                }
            )
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--launch", action="store_true", help="Headless CloakBrowser probe")
    args = ap.parse_args()
    if args.launch:
        rows = probe_live()
    else:
        rows = probe_flags()
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
