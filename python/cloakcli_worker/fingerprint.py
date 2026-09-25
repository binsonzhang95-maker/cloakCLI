"""Persistent per-profile CloakBrowser fingerprint seed, persona, and geo alignment.

CloakBrowser's get_default_stealth_args() picks random.randint(10000, 99999)
for --fingerprint=<seed> on every launch. Same profile cookie jar + rotating
UA/GPU fingerprint looks like a new visitor. We mint one seed per profile,
persist it in profiles/<name>/profile.json as fingerprint_seed, and pass
args=["--fingerprint=<seed>"] so cloakbrowser build_args dedupes by flag key
and overrides the random default (platform=windows stealth stays).

Persona flags (brand / platform_version / hardware / screen) are minted
deterministically from that seed against a verified whitelist. GeoIP timezone
is resolved from the proxy exit IP (City DB IANA) with a cache that invalidates
when the exit IP, proxy session identity, or GeoLite DB version changes.

Never log proxy URLs, passwords, emails, or cookie values.
Never set Playwright user_agent — that desyncs HTTP UA / Client Hints /
JS userAgentData. Brand identity is binary flags only.

WebRTC ICE verification (follow-up test; not asserted here):
  Launch with proxy, then in the page:
    const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
    pc.createDataChannel('x');
    pc.onicecandidate = e => console.log(e.candidate && e.candidate.candidate);
    pc.createOffer().then(o => pc.setLocalDescription(o));
  Host ICE candidates should carry --fingerprint-webrtc-ip (the echo-verified
  exit IP). A host candidate with the machine's real IP is a leak.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SEED_MIN = 10000
SEED_MAX = 99999

_FIELD = "fingerprint_seed"
_PERSONA_FIELD = "fingerprint_persona"
_GEO_FIELD = "geo_cache"
_LANGUAGE_FIELD = "language"

PERSONA_SCHEMA = 1
GEO_CACHE_SCHEMA = 1
GEO_CACHE_TTL = timedelta(days=30)

# Windows Client Hints platform versions (UA-CH reduced form).
# 10.0.0 = Windows 10; 15.0.0 = Windows 11. Do not invent build numbers.
_PLATFORM_WINDOWS = "windows"
_PLATFORM_VERSIONS_WINDOWS = ("10.0.0", "15.0.0")

# Reasonable Windows desktop pairs. deviceMemory is the Device Memory API
# bucket (0.25/0.5/1/2/4/8); 8 is the public Chrome cap. hardwareConcurrency
# varies so the fleet is not stuck at 8/8.
HW_MEMORY_COMBOS: tuple[tuple[int, int], ...] = (
    (4, 4),
    (4, 8),
    (6, 8),
    (8, 8),
    (12, 8),
    (16, 8),
)

# Screen presets: (width, height, taskbar_px, chrome_ui_px).
# viewport = (width, height - taskbar - chrome_ui); available = (width, height - taskbar).
# deviceScaleFactor is 1 — the 146 binary has no DPR flag, so HiDPI would desync.
SCREEN_PRESETS: tuple[tuple[int, int, int, int], ...] = (
    (1920, 1080, 48, 85),  # matches cloakbrowser DEFAULT_VIEWPORT innerHeight 947
    (1366, 768, 40, 82),
    (1536, 864, 40, 82),
    (2560, 1440, 48, 85),
    (1280, 720, 40, 80),
    (1920, 1200, 48, 85),
)

class PersonaWhitelistError(ValueError):
    """Persona is not on the verified (brand, version, chromium, platform) list."""


class GeoResolutionError(RuntimeError):
    """Fail-closed geo lookup — do not launch with the host timezone."""


def mint_fingerprint_seed() -> int:
    """Return a new seed in [SEED_MIN, SEED_MAX] inclusive."""
    return random.randint(SEED_MIN, SEED_MAX)


def is_valid_fingerprint_seed(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return SEED_MIN <= value <= SEED_MAX
    if isinstance(value, float) and value.is_integer():
        return SEED_MIN <= int(value) <= SEED_MAX
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return SEED_MIN <= n <= SEED_MAX
    return False


def coerce_fingerprint_seed(value: Any) -> int | None:
    if not is_valid_fingerprint_seed(value):
        return None
    if isinstance(value, str):
        return int(value.strip())
    return int(value)


def deployed_chromium_version() -> str:
    """Public Chromium version of the installed binary (no CloakBrowser patch suffix).

    cloakbrowser reports e.g. 146.0.7680.177.5; Chrome UA/CH use 146.0.7680.177.
    """
    try:
        from cloakbrowser.config import get_chromium_version

        raw = str(get_chromium_version() or "").strip()
    except Exception:
        raw = ""
    parts = [p for p in raw.split(".") if p.isdigit()]
    if len(parts) >= 4:
        return ".".join(parts[:4])
    if parts:
        return parts[0] + ".0.0.0"
    return "146.0.7680.177"


def deployed_chromium_major() -> str:
    return deployed_chromium_version().split(".", 1)[0]


def verified_brand_tuples() -> tuple[tuple[str, str, str, str, str], ...]:
    """Verified coherent (brand, brand_version, chromium_compatible, platform, platform_version).

    Opera/Vivaldi-on-146 are not enabled: product version is not the Chromium
    kernel, and CH/UA coherence is unverified on this binary. Edge is omitted
    for the same reason. Diversity comes from platform_version / hw / screen / geo.
    """
    compatible = deployed_chromium_version()
    # brand_version is the public Chromium version (e.g. 146.0.7680.177).
    # The 146 binary keeps UA reduced (Chrome/146.0.0.0) and fills high-entropy
    # CH uaFullVersion / fullVersionList from this flag. Passing major-only
    # "146" leaves high-entropy CH as "146", which is less like stock Chrome.
    return tuple(
        ("Chrome", compatible, compatible, _PLATFORM_WINDOWS, pv)
        for pv in _PLATFORM_VERSIONS_WINDOWS
    )


def is_valid_iana_timezone(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def is_valid_locale(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or len(s) > 16:
        return False
    parts = s.replace("_", "-").split("-")
    if not parts or not parts[0].isalpha() or not (2 <= len(parts[0]) <= 3):
        return False
    if len(parts) >= 2 and not (parts[1].isalnum() and 2 <= len(parts[1]) <= 4):
        return False
    return True


def normalize_locale(value: str) -> str:
    parts = value.strip().replace("_", "-").split("-")
    lang = parts[0].lower()
    if len(parts) == 1:
        return lang
    rest = [parts[1].upper() if len(parts[1]) == 2 else parts[1]]
    rest.extend(parts[2:])
    return "-".join([lang, *rest])


def _screen_inner(width: int, height: int, taskbar: int, chrome_ui: int) -> tuple[int, int, int, int]:
    inner_h = height - taskbar - chrome_ui
    avail_h = height - taskbar
    if inner_h < 400 or avail_h < 480 or width < 1024:
        raise ValueError(f"incoherent screen preset {width}x{height} taskbar={taskbar} ui={chrome_ui}")
    return width, inner_h, width, avail_h


def mint_fingerprint_persona(seed: int) -> dict[str, Any]:
    """Deterministic persona from seed, drawn only from the verified whitelist."""
    if not is_valid_fingerprint_seed(seed):
        raise ValueError(f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {seed!r}")
    rng = random.Random(int(seed))
    tuples = verified_brand_tuples()
    brand, brand_version, chromium_compatible, platform, platform_version = rng.choice(tuples)
    hw, mem = rng.choice(HW_MEMORY_COMBOS)
    sw, sh, taskbar, chrome_ui = rng.choice(SCREEN_PRESETS)
    vw, vh, aw, ah = _screen_inner(sw, sh, taskbar, chrome_ui)
    return {
        "schema": PERSONA_SCHEMA,
        "brand": brand,
        "brand_version": brand_version,
        "chromium_compatible": chromium_compatible,
        "platform": platform,
        "platform_version": platform_version,
        "hardware_concurrency": hw,
        "device_memory": mem,
        "screen_width": sw,
        "screen_height": sh,
        "device_scale_factor": 1,
        "taskbar_height": taskbar,
        "chrome_ui_height": chrome_ui,
        "viewport_width": vw,
        "viewport_height": vh,
        "available_width": aw,
        "available_height": ah,
    }


def persona_brand_tuple(persona: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(persona.get("brand") or ""),
        str(persona.get("brand_version") or ""),
        str(persona.get("chromium_compatible") or ""),
        str(persona.get("platform") or ""),
        str(persona.get("platform_version") or ""),
    )


def _screen_coherent(persona: dict[str, Any]) -> bool:
    try:
        sw = int(persona["screen_width"])
        sh = int(persona["screen_height"])
        taskbar = int(persona["taskbar_height"])
        chrome_ui = int(persona.get("chrome_ui_height") or 0)
        dpr = persona.get("device_scale_factor", 1)
        vw = int(persona["viewport_width"])
        vh = int(persona["viewport_height"])
        aw = int(persona["available_width"])
        ah = int(persona["available_height"])
    except (KeyError, TypeError, ValueError):
        return False
    if dpr not in (1, 1.0):
        return False
    try:
        evw, evh, eaw, eah = _screen_inner(sw, sh, taskbar, chrome_ui)
    except ValueError:
        return False
    return (vw, vh, aw, ah) == (evw, evh, eaw, eah)


def is_whitelisted_persona(persona: Any) -> bool:
    if not isinstance(persona, dict):
        return False
    if persona.get("schema") not in (PERSONA_SCHEMA, None):
        return False
    if persona_brand_tuple(persona) not in verified_brand_tuples():
        return False
    try:
        hw = int(persona["hardware_concurrency"])
        mem = int(persona["device_memory"])
        sw = int(persona["screen_width"])
        sh = int(persona["screen_height"])
        taskbar = int(persona["taskbar_height"])
        chrome_ui = int(persona.get("chrome_ui_height") or 0)
    except (KeyError, TypeError, ValueError):
        return False
    if (hw, mem) not in HW_MEMORY_COMBOS:
        return False
    if (sw, sh, taskbar, chrome_ui) not in SCREEN_PRESETS:
        return False
    return _screen_coherent(persona)


def validate_persona(persona: Any) -> dict[str, Any]:
    if not is_whitelisted_persona(persona):
        raise PersonaWhitelistError(
            f"persona not on verified Chrome/Windows whitelist: {persona_brand_tuple(persona) if isinstance(persona, dict) else type(persona).__name__}"
        )
    assert isinstance(persona, dict)
    return persona


def fingerprint_chrome_args(
    seed: int,
    persona: dict[str, Any] | None = None,
    *,
    geo: dict[str, Any] | None = None,
    language: str | None = None,
) -> list[str]:
    """Chrome args that override cloakbrowser's random --fingerprint default.

    Always includes the verified persona flags so HTTP UA / CH / JS stay on
    the same Chrome identity. Playwright user_agent is never emitted.
    """
    if not is_valid_fingerprint_seed(seed):
        raise ValueError(f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {seed!r}")
    if persona is None:
        persona = mint_fingerprint_persona(int(seed))
    else:
        persona = validate_persona(persona)

    args = [
        f"--fingerprint={int(seed)}",
        f"--fingerprint-platform={persona['platform']}",
        f"--fingerprint-platform-version={persona['platform_version']}",
        f"--fingerprint-brand={persona['brand']}",
        f"--fingerprint-brand-version={persona['brand_version']}",
        f"--fingerprint-hardware-concurrency={persona['hardware_concurrency']}",
        f"--fingerprint-device-memory={persona['device_memory']}",
        f"--fingerprint-screen-width={persona['screen_width']}",
        f"--fingerprint-screen-height={persona['screen_height']}",
        f"--fingerprint-taskbar-height={persona['taskbar_height']}",
    ]
    lang = language or (geo or {}).get("locale") or (geo or {}).get("locale_from_geo")
    if lang and is_valid_locale(lang):
        lang = normalize_locale(lang)
        args.append(f"--lang={lang}")
        args.append(f"--fingerprint-locale={lang}")
    tz = (geo or {}).get("timezone")
    if tz and is_valid_iana_timezone(tz):
        args.append(f"--fingerprint-timezone={tz}")
    exit_ip = (geo or {}).get("exit_ip")
    if exit_ip:
        args.append(f"--fingerprint-webrtc-ip={exit_ip}")
    return args


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_profile_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.is_file():
        return {}
    try:
        loaded = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def ensure_fingerprint_seed(meta_path: Path, *, regenerate: bool = False) -> int:
    """Load profile.json; mint/persist fingerprint_seed if missing/invalid or regenerate.

    Preserves all other fields (proxy, notes, etc.). Returns the seed to use.
    A reminted seed also remints fingerprint_persona from that seed.
    """
    meta_path = Path(meta_path)
    data = _read_profile_meta(meta_path)

    existing = coerce_fingerprint_seed(data.get(_FIELD))
    if existing is not None and not regenerate:
        return existing

    seed = mint_fingerprint_seed()
    data[_FIELD] = seed
    data[_PERSONA_FIELD] = mint_fingerprint_persona(seed)
    _atomic_write_json(meta_path, data)
    return seed


def ensure_fingerprint_persona(
    meta_path: Path,
    seed: int,
    *,
    regenerate: bool = False,
) -> dict[str, Any]:
    """Load or mint a whitelist persona for this seed; persist on profile.json."""
    meta_path = Path(meta_path)
    data = _read_profile_meta(meta_path)
    existing = data.get(_PERSONA_FIELD)
    if not regenerate and is_whitelisted_persona(existing):
        assert isinstance(existing, dict)
        return existing
    if existing is not None and not is_whitelisted_persona(existing):
        sys.stderr.write(
            "[fingerprint] rejected non-whitelist persona; reminting from seed\n"
        )
        sys.stderr.flush()
    persona = mint_fingerprint_persona(seed)
    data[_FIELD] = int(seed)
    data[_PERSONA_FIELD] = persona
    _atomic_write_json(meta_path, data)
    return persona


def ensure_language_preference(
    meta_path: Path,
    *,
    geo_locale: str | None = None,
) -> str | None:
    """Persist language as a profile preference. Never overwrite from country."""
    meta_path = Path(meta_path)
    data = _read_profile_meta(meta_path)
    existing = data.get(_LANGUAGE_FIELD)
    if is_valid_locale(existing):
        return normalize_locale(str(existing))
    if geo_locale and is_valid_locale(geo_locale):
        lang = normalize_locale(geo_locale)
        data[_LANGUAGE_FIELD] = lang
        _atomic_write_json(meta_path, data)
        return lang
    return None


def find_meta_path_for_user_data_dir(
    profiles_root: Path,
    user_data_dir: str | Path,
) -> Path | None:
    """Best-effort match profiles/*/profile.json by user_data_dir field or path heuristics.

    Matching is by resolved path when possible, else by string equality / basename
    suffix (e.g. data/profiles/<name>-pinterest-run ↔ profiles/<name>/profile.json).
    Does not create files.
    """
    profiles_root = Path(profiles_root)
    if not profiles_root.is_dir():
        return None

    target = Path(user_data_dir)
    try:
        target_resolved = target.resolve()
    except OSError:
        target_resolved = target

    target_str = str(user_data_dir).replace("\\", "/").rstrip("/")
    target_name = target.name

    candidates: list[Path] = []
    try:
        entries = sorted(profiles_root.iterdir(), key=lambda p: p.name)
    except OSError:
        return None

    for entry in entries:
        if not entry.is_dir():
            continue
        meta = entry / "profile.json"
        if not meta.is_file():
            continue
        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        ud = raw.get("user_data_dir")
        if isinstance(ud, str) and ud.strip():
            ud_path = Path(ud)
            if not ud_path.is_absolute():
                # Relative to CloakCLI root (parent of profiles/)
                ud_path = profiles_root.parent / ud
            try:
                if ud_path.resolve() == target_resolved:
                    return meta
            except OSError:
                pass
            if ud.replace("\\", "/").rstrip("/") == target_str:
                return meta
            if Path(ud).name == target_name:
                candidates.append(meta)
        # Heuristic: data/profiles/<name>-pinterest-run or <name>
        name = entry.name
        if target_name in (name, f"{name}-pinterest-run", f"{name}-run"):
            candidates.append(meta)

    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_fingerprint_seed(
    *,
    fingerprint_seed: int | None = None,
    profile_meta_path: str | Path | None = None,
    profiles_root: str | Path | None = None,
    user_data_dir: str | Path | None = None,
    regenerate: bool = False,
) -> int | None:
    """Resolve a seed from explicit value, meta path, or user_data_dir lookup.

    Returns None when no profile.json can be found (caller may launch without
    override; cloakbrowser will still pick a random seed for that launch).
    When fingerprint_seed is passed explicitly it wins (must be in range);
    regenerate only applies when loading/ensuring via profile.json.
    """
    if fingerprint_seed is not None:
        coerced = coerce_fingerprint_seed(fingerprint_seed)
        if coerced is None:
            raise ValueError(
                f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {fingerprint_seed!r}"
            )
        return coerced

    if profile_meta_path is not None:
        return ensure_fingerprint_seed(Path(profile_meta_path), regenerate=regenerate)

    if profiles_root is not None and user_data_dir is not None:
        meta = find_meta_path_for_user_data_dir(Path(profiles_root), user_data_dir)
        if meta is not None:
            return ensure_fingerprint_seed(meta, regenerate=regenerate)

    return None


def resolve_profile_meta_path(
    *,
    profile_meta_path: str | Path | None = None,
    profiles_root: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> Path | None:
    if profile_meta_path is not None:
        return Path(profile_meta_path)
    if profiles_root is not None and user_data_dir is not None:
        return find_meta_path_for_user_data_dir(Path(profiles_root), user_data_dir)
    return None


def log_fingerprint_seed(seed: int) -> None:
    """Safe stderr line — seed only, never proxy/email/cookies."""
    sys.stderr.write(f"[fingerprint] seed={int(seed)}\n")
    sys.stderr.flush()


def log_fingerprint_persona(persona: dict[str, Any]) -> None:
    sys.stderr.write(
        "[fingerprint] "
        f"brand={persona.get('brand')}/{persona.get('brand_version')} "
        f"platform={persona.get('platform')}/{persona.get('platform_version')} "
        f"hw={persona.get('hardware_concurrency')}/{persona.get('device_memory')} "
        f"screen={persona.get('screen_width')}x{persona.get('screen_height')}\n"
    )
    sys.stderr.flush()


def is_register_launch(skill_name: str | None = None, skill_path: str | None = None) -> bool:
    blob = f"{skill_name or ''} {skill_path or ''}".replace("\\", "/").lower()
    return "register" in blob


def env_require_geo() -> bool | None:
    raw = os.environ.get("CLOAKCLI_REQUIRE_GEO", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def proxy_session_identity(proxy: str) -> str:
    """Hash of scheme+username+host+port. Password is excluded. Never log `proxy`."""
    parsed = urlparse(proxy)
    material = "|".join(
        [
            parsed.scheme or "",
            parsed.username or "",
            (parsed.hostname or "").lower(),
            str(parsed.port or ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def geo_db_path() -> Path | None:
    try:
        from cloakbrowser.geoip import _ensure_geoip_db

        path = _ensure_geoip_db()
    except ImportError as e:
        raise GeoResolutionError(
            "GEO_DB_MISSING: geoip2 is required (pip install 'cloakbrowser[geoip]')"
        ) from e
    except GeoResolutionError:
        raise
    except Exception as e:
        raise GeoResolutionError(f"GEO_DB_MISSING: {type(e).__name__}") from e
    return Path(path) if path is not None else None


def read_geo_db_version(db_path: Path | None = None) -> str:
    path = db_path if db_path is not None else geo_db_path()
    if path is None:
        raise GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable")
    try:
        import geoip2.database

        with geoip2.database.Reader(str(path)) as reader:
            epoch = int(reader.metadata().build_epoch)
            return f"geolite2-city:{epoch}"
    except GeoResolutionError:
        raise
    except Exception:
        try:
            return f"mtime:{int(path.stat().st_mtime)}"
        except OSError as e:
            raise GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unreadable") from e


def default_resolve_exit_ip(proxy: str) -> str:
    """Echo-verified egress IP. Refuses proxy-hostname fallback (not a verified exit)."""
    try:
        from cloakbrowser.geoip import resolve_proxy_exit_ip
    except ImportError as e:
        raise GeoResolutionError(
            "GEO_DB_MISSING: geoip2/httpx required for exit-IP echo"
        ) from e
    try:
        ip = resolve_proxy_exit_ip(proxy)
    except Exception as e:
        raise GeoResolutionError(f"GEO_TIMEOUT: exit-IP echo failed ({type(e).__name__})") from e
    if not ip:
        raise GeoResolutionError("GEO_TIMEOUT: could not discover proxy exit IP via echo")
    if not _is_public_ip(ip):
        raise GeoResolutionError(
            "GEO_EXIT_IP: echo returned a non-public address; refusing proxy-host fallback"
        )
    return ip


def default_lookup_city(ip: str) -> dict[str, Any]:
    """City-DB lookup for an already-verified public exit IP. IANA timezone required."""
    path = geo_db_path()
    if path is None:
        raise GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable")
    try:
        import geoip2.database
        from cloakbrowser.geoip import COUNTRY_LOCALE_MAP
    except ImportError as e:
        raise GeoResolutionError(
            "GEO_DB_MISSING: geoip2 is required (pip install 'cloakbrowser[geoip]')"
        ) from e
    try:
        with geoip2.database.Reader(str(path)) as reader:
            resp = reader.city(ip)
    except GeoResolutionError:
        raise
    except Exception as e:
        raise GeoResolutionError(f"GEO_LOOKUP: City DB failed for exit IP ({type(e).__name__})") from e
    tz = getattr(getattr(resp, "location", None), "time_zone", None)
    country = getattr(getattr(resp, "country", None), "iso_code", None)
    if not is_valid_iana_timezone(tz):
        raise GeoResolutionError("GEO_TIMEZONE: City DB returned missing/unknown IANA timezone")
    locale = None
    if isinstance(country, str) and country:
        mapped = COUNTRY_LOCALE_MAP.get(country)
        if mapped and is_valid_locale(mapped):
            locale = normalize_locale(mapped)
    return {
        "timezone": str(tz).strip(),
        "country": country if isinstance(country, str) else None,
        "locale_from_geo": locale,
    }


def geo_cache_valid(
    cache: Any,
    *,
    proxy_identity: str,
    exit_ip: str,
    geo_db_version: str,
    now: datetime | None = None,
) -> bool:
    if not isinstance(cache, dict):
        return False
    if cache.get("schema") not in (GEO_CACHE_SCHEMA, None):
        return False
    if cache.get("proxy_identity") != proxy_identity:
        return False
    if cache.get("exit_ip") != exit_ip:
        return False
    if cache.get("geo_db_version") != geo_db_version:
        return False
    if not is_valid_iana_timezone(cache.get("timezone")):
        return False
    if not _is_public_ip(str(cache.get("exit_ip") or "")):
        return False
    raw_ts = cache.get("looked_up_at")
    if not isinstance(raw_ts, str) or not raw_ts.strip():
        return False
    try:
        looked = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if looked.tzinfo is None:
        looked = looked.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now - looked > GEO_CACHE_TTL:
        return False
    return True


def resolve_geo_for_launch(
    *,
    proxy: str,
    meta_path: Path | None = None,
    require_geo: bool = False,
    now: datetime | None = None,
    resolve_exit_ip: Callable[[str], str] | None = None,
    lookup_city: Callable[[str], dict[str, Any]] | None = None,
    db_version: str | None = None,
) -> dict[str, Any]:
    """Verify exit IP, reuse geo_cache when valid, otherwise City-DB re-resolve.

    Fail-closed when require_geo is True. Never falls back to the host timezone.
    """
    resolve_ip = resolve_exit_ip or default_resolve_exit_ip
    lookup = lookup_city or default_lookup_city
    now = now or datetime.now(timezone.utc)
    identity = proxy_session_identity(proxy)

    def _fail(err: GeoResolutionError) -> dict[str, Any]:
        if require_geo:
            raise err
        sys.stderr.write(f"[geo] skip: {err}\n")
        sys.stderr.flush()
        return {}

    try:
        exit_ip = resolve_ip(proxy)
        version = db_version if db_version is not None else read_geo_db_version()
    except GeoResolutionError as e:
        return _fail(e)

    data: dict[str, Any] = _read_profile_meta(meta_path) if meta_path is not None else {}
    cached = data.get(_GEO_FIELD)
    if geo_cache_valid(
        cached,
        proxy_identity=identity,
        exit_ip=exit_ip,
        geo_db_version=version,
        now=now,
    ):
        assert isinstance(cached, dict)
        sys.stderr.write("[geo] cache=hit\n")
        sys.stderr.flush()
        return dict(cached)

    try:
        city = lookup(exit_ip)
    except GeoResolutionError as e:
        return _fail(e)

    tz = city.get("timezone")
    if not is_valid_iana_timezone(tz):
        return _fail(GeoResolutionError("GEO_TIMEZONE: City DB returned missing/unknown IANA timezone"))

    record = {
        "schema": GEO_CACHE_SCHEMA,
        "proxy_identity": identity,
        "exit_ip": exit_ip,
        "timezone": str(tz).strip(),
        "locale_from_geo": city.get("locale_from_geo"),
        "country": city.get("country"),
        "looked_up_at": now.astimezone(timezone.utc).isoformat(),
        "geo_db_version": version,
    }
    if meta_path is not None:
        data = _read_profile_meta(meta_path)
        data[_GEO_FIELD] = record
        try:
            _atomic_write_json(meta_path, data)
        except OSError as e:
            if require_geo:
                raise GeoResolutionError(f"GEO_PERSIST: {type(e).__name__}") from e
    sys.stderr.write(
        f"[geo] cache=miss tz={record['timezone']} lang_geo={record.get('locale_from_geo') or '-'}\n"
    )
    sys.stderr.flush()
    return record


def apply_to_launch_kwargs(
    kwargs: dict[str, Any],
    *,
    seed: int | None,
    proxy: str | None = None,
    headed: bool = False,
    profile_meta_path: str | Path | None = None,
    require_geo: bool | None = None,
    resolve_exit_ip: Callable[[str], str] | None = None,
    lookup_city: Callable[[str], dict[str, Any]] | None = None,
    db_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fill launch_persistent_context kwargs with persona flags + geoip alignment.

    Sets binary flags only. Passes geoip=True when a proxy is present and geo
    resolved (cloakbrowser still injects WebRTC if we did not already set it).
    """
    meta = Path(profile_meta_path) if profile_meta_path is not None else None
    persona: dict[str, Any] | None = None
    if seed is not None:
        if meta is not None:
            persona = ensure_fingerprint_persona(meta, seed)
        else:
            persona = mint_fingerprint_persona(seed)
        log_fingerprint_persona(persona)

    env_geo = env_require_geo()
    if require_geo is None:
        require_geo = bool(env_geo) if env_geo is not None else False

    geo: dict[str, Any] = {}
    language: str | None = None
    if meta is not None:
        language = ensure_language_preference(meta)

    proxy_url = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None
    if proxy_url:
        geo = resolve_geo_for_launch(
            proxy=proxy_url,
            meta_path=meta,
            require_geo=bool(require_geo),
            now=now,
            resolve_exit_ip=resolve_exit_ip,
            lookup_city=lookup_city,
            db_version=db_version,
        )
        if meta is not None and geo:
            language = ensure_language_preference(
                meta, geo_locale=geo.get("locale_from_geo")
            )
        if geo.get("timezone"):
            # Explicit equivalent of geoip=True: timezone/locale flags + WebRTC IP.
            # Also set geoip=True so cloakbrowser's launch path stays on the
            # documented proxy+geoip alignment (it will not clobber explicit tz/locale).
            kwargs["geoip"] = True
            kwargs["timezone"] = geo["timezone"]
            if language:
                kwargs["locale"] = language
            elif geo.get("locale_from_geo"):
                kwargs["locale"] = geo["locale_from_geo"]

    if seed is not None:
        kwargs["args"] = fingerprint_chrome_args(
            seed, persona, geo=geo or None, language=language
        )
    elif geo or language:
        extra: list[str] = []
        if language and is_valid_locale(language):
            lang = normalize_locale(language)
            extra.extend([f"--lang={lang}", f"--fingerprint-locale={lang}"])
        tz = geo.get("timezone")
        if tz and is_valid_iana_timezone(tz):
            extra.append(f"--fingerprint-timezone={tz}")
        exit_ip = geo.get("exit_ip")
        if exit_ip:
            extra.append(f"--fingerprint-webrtc-ip={exit_ip}")
        if extra:
            kwargs["args"] = extra

    if persona is not None and not headed:
        kwargs["viewport"] = {
            "width": int(persona["viewport_width"]),
            "height": int(persona["viewport_height"]),
        }

    return kwargs
