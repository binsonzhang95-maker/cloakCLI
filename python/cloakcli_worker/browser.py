"""CloakBrowser launch helpers + cookie injection (no value logging)."""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class InvalidCookieError(ValueError):
    """Stable INVALID_COOKIE errors for worker → CLI."""

    def __init__(self, message: str) -> None:
        if not message.startswith("INVALID_COOKIE"):
            message = f"INVALID_COOKIE: {message}"
        super().__init__(message)


def launch_context(
    *,
    user_data_dir: str,
    headed: bool = False,
    proxy: str | None = None,
    user_agent: str | None = None,
    extension_paths: list[str] | None = None,
    fingerprint_seed: int | None = None,
    profile_meta_path: str | Path | None = None,
    profiles_root: str | Path | None = None,
    require_geo: bool | None = None,
    skill_name: str | None = None,
    skill_path: str | None = None,
    browser_version: str | None = None,
) -> Any:
    """Launch persistent stealth Chromium via cloakbrowser.

    When a fingerprint_seed is known (explicit, profile_meta_path, or best-effort
    match of user_data_dir under profiles_root), pass binary fingerprint flags
    so cloakbrowser build_args overrides the per-launch random default. Persona
    (Chrome brand + platform_version + hw/screen/GPU) is minted from the seed.
    With a proxy, geoip is resolved against the echo-verified exit IP and
    timezone/locale/WebRTC flags are set; register paths fail closed on geo
    failure (no host timezone fallback). Register/strict also fail-closed when
    Windows minimum fonts are missing (nurture warns only; no silent MS font
    install). Headed launches emit --window-size from persona screen (not
    maximize-only). Post-launch ICE: when require_geo is true and --fingerprint-webrtc-ip is
    set, launch_context gathers ICE (page/iframe/worker) and fail-closes on
    host leak (opt out with CLOAKCLI_REQUIRE_WEBRTC_ICE=0). Playwright user_agent is not
    used for persona (it desyncs HTTP UA / Client Hints / JS userAgentData).
    Dual-bin mode persists exact browser_version on the profile and always
    passes it into cloakbrowser; persona/UA/CH follow the bound binary.
    """
    from cloakbrowser import launch_persistent_context

    from .fingerprint import (
        BrowserVersionError,
        apply_to_launch_kwargs,
        dual_bin_mode,
        env_require_geo,
        is_register_launch,
        log_fingerprint_seed,
        resolve_browser_version_for_launch,
        resolve_fingerprint_seed,
        resolve_profile_meta_path,
    )

    kwargs: dict[str, Any] = {
        "user_data_dir": user_data_dir,
        "headless": not headed,
    }
    if proxy:
        kwargs["proxy"] = proxy
    # Do not apply Playwright user_agent for fingerprint identity. Forwarding
    # a caller override still desyncs CH, so it is only kept when no seed/persona
    # will be attached (handled below after seed resolve).
    if extension_paths:
        kwargs["extension_paths"] = list(extension_paths)

    seed = resolve_fingerprint_seed(
        fingerprint_seed=fingerprint_seed,
        profile_meta_path=profile_meta_path,
        profiles_root=profiles_root,
        user_data_dir=user_data_dir,
    )
    meta = resolve_profile_meta_path(
        profile_meta_path=profile_meta_path,
        profiles_root=profiles_root,
        user_data_dir=user_data_dir,
    )
    if require_geo is None:
        env_geo = env_require_geo()
        if env_geo is not None:
            require_geo = env_geo
        else:
            require_geo = is_register_launch(skill_name, skill_path)

    # Dual-bin: always resolve exact pin before launch (forbid unpinned).
    if seed is not None or proxy:
        apply_to_launch_kwargs(
            kwargs,
            seed=seed,
            proxy=proxy,
            headed=headed,
            profile_meta_path=meta,
            require_geo=require_geo,
            browser_version=browser_version,
        )
    else:
        kwargs["browser_version"] = resolve_browser_version_for_launch(
            browser_version=browser_version,
            profile_meta_path=meta,
            seed=seed,
        )
    if seed is not None:
        log_fingerprint_seed(seed)
    if user_agent and seed is None:
        # Dual-bin / pinned launch: never forward Playwright user_agent — it
        # desyncs CH/JS from the bound binary (fake multi-version). Fail closed.
        if dual_bin_mode() or kwargs.get("browser_version"):
            raise BrowserVersionError(
                "BROWSER_VERSION: Playwright user_agent forbidden under dual-bin "
                "/ pinned browser_version (UA/CH must follow the bound binary)"
            )
        kwargs["user_agent"] = user_agent

    ctx = launch_persistent_context(**kwargs)

    # Strict register / require_geo: post-launch ICE host must equal echo exit IP.
    # Skip when no --fingerprint-webrtc-ip (no proxy geo) or when ops opts out.
    if require_geo and _env_require_webrtc_ice():
        exit_ip = _webrtc_ip_from_args(kwargs.get("args") or [])
        if exit_ip:
            from .fingerprint import verify_webrtc_ice_no_leak

            page = get_page(ctx)
            try:
                verify_webrtc_ice_no_leak(page, exit_ip)
            except Exception:
                try:
                    ctx.close()
                except Exception:
                    pass
                raise

    return ctx


def _webrtc_ip_from_args(args: list[str]) -> str | None:
    for arg in args:
        if isinstance(arg, str) and arg.startswith("--fingerprint-webrtc-ip="):
            ip = arg.split("=", 1)[1].strip()
            return ip or None
    return None


def _env_require_webrtc_ice() -> bool:
    """Default ON for strict paths that already set require_geo; allow opt-out."""
    raw = os.environ.get("CLOAKCLI_REQUIRE_WEBRTC_ICE", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    # empty / unset → enabled when caller already chose require_geo
    return True


def get_page(ctx: Any) -> Any:
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def _validate_cookie_entry(c: Any, index: int) -> dict[str, Any]:
    if not isinstance(c, dict):
        raise InvalidCookieError(f"cookie[{index}] must be an object")
    name = c.get("name")
    if not isinstance(name, str) or not name:
        raise InvalidCookieError(f"cookie[{index}] missing name")
    if "value" not in c:
        raise InvalidCookieError(f"cookie[{index}] missing value")
    domain = c.get("domain")
    url = c.get("url")
    has_domain = isinstance(domain, str) and bool(domain)
    has_url = isinstance(url, str) and bool(url)
    if not has_domain and not has_url:
        raise InvalidCookieError(f"cookie[{index}] needs domain or url")
    return c


def validate_storage_state(data: Any) -> tuple[list[dict[str, Any]], list[Any]]:
    """Full schema validation. Never trust file blindly. Raises InvalidCookieError."""
    if not isinstance(data, dict):
        raise InvalidCookieError("cookie file must be a storage_state object")

    cookies_raw = data.get("cookies")
    if cookies_raw is None:
        cookies_raw = []
    origins = data.get("origins")
    if origins is None:
        origins = []
    if not isinstance(cookies_raw, list):
        raise InvalidCookieError("cookies must be an array")
    if not isinstance(origins, list):
        raise InvalidCookieError("origins must be an array")

    cookies: list[dict[str, Any]] = []
    for i, c in enumerate(cookies_raw):
        cookies.append(_validate_cookie_entry(c, i))

    for i, o in enumerate(origins):
        if not isinstance(o, dict):
            raise InvalidCookieError(f"origins[{i}] must be an object")

    return cookies, origins


def apply_cookie_file(ctx: Any, cookie_file: str) -> dict[str, Any]:
    """Load Playwright storage_state JSON and inject cookies.

    Origins/localStorage injection is **disabled by default** (MVP security —
    arbitrary page.goto to imported origins is an injection/network boundary).
    Opt-in: set CLOAKCLI_APPLY_ORIGINS=1 for strict http(s) allowlist only
    (rejects localhost / private / link-local / non-http(s)).

    Returns metadata only — never cookie values.
    """
    path = Path(cookie_file)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise InvalidCookieError(f"unreadable cookie file: {type(e).__name__}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InvalidCookieError(f"cookie file is not valid JSON: {e.msg}") from e

    cookies, origins = validate_storage_state(data)

    applied = 0
    if cookies:
        # Playwright add_cookies — do not log contents
        try:
            ctx.add_cookies(cookies)
        except Exception as e:
            # Do not include cookie payloads in error text
            raise InvalidCookieError(
                f"add_cookies failed: {type(e).__name__}"
            ) from e
        applied = len(cookies)

    origins_applied = 0
    apply_origins = os.environ.get("CLOAKCLI_APPLY_ORIGINS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if origins and apply_origins:
        origins_applied = _apply_origins_strict(ctx, origins)
    elif origins:
        sys.stderr.write(
            f"[cookies] origins present count={len(origins)} but injection disabled "
            f"(set CLOAKCLI_APPLY_ORIGINS=1 for strict allowlist apply)\n"
        )
        sys.stderr.flush()

    domains: set[str] = set()
    for c in cookies:
        d = c.get("domain")
        if isinstance(d, str) and d:
            domains.add(d)

    meta = {
        "cookies_applied": applied,
        "origins_applied": origins_applied,
        "origins_skipped": len(origins) if origins and not apply_origins else 0,
        "domains": sorted(domains),
    }
    # Safe stderr status (no values)
    sys.stderr.write(
        f"[cookies] applied count={applied} origins={origins_applied} "
        f"domains={len(domains)}\n"
    )
    sys.stderr.flush()
    return meta


def _is_blocked_host(host: str) -> bool:
    h = host.strip().lower().rstrip(".")
    if not h:
        return True
    if h == "localhost" or h.endswith(".localhost"):
        return True
    # Strip brackets for IPv6 literals
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        ip = ipaddress.ip_address(h)
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        # hostname — block obvious local names
        if h in ("localhost", "ip6-localhost", "ip6-loopback"):
            return True
        return False


def _origin_allowed(origin_url: str) -> bool:
    try:
        parsed = urlparse(origin_url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    if _is_blocked_host(parsed.hostname):
        return False
    # Disallow credentials in origin
    if parsed.username or parsed.password:
        return False
    return True


def _apply_origins_strict(ctx: Any, origins: list[Any]) -> int:
    """Strict allowlist localStorage apply (opt-in only). No arbitrary navigation."""
    n = 0
    page = get_page(ctx)
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        origin_url = origin.get("origin")
        items = origin.get("localStorage") or []
        if not isinstance(origin_url, str) or not origin_url:
            continue
        if not isinstance(items, list):
            continue
        if not _origin_allowed(origin_url):
            sys.stderr.write(
                "[cookies] origin apply skipped: blocked or non-http(s) origin\n"
            )
            sys.stderr.flush()
            continue
        try:
            page.goto(origin_url, wait_until="domcontentloaded", timeout=15000)
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                value = item.get("value")
                if name is None or value is None:
                    continue
                page.evaluate(
                    """([k, v]) => { try { localStorage.setItem(k, v); } catch (e) {} }""",
                    [str(name), str(value)],
                )
            n += 1
        except Exception as e:
            sys.stderr.write(f"[cookies] origin apply skipped: {type(e).__name__}\n")
            sys.stderr.flush()
    return n
