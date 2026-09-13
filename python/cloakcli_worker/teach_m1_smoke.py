"""Headed M1 smoke sequence run inside the Teach worker process.

Driven by CLOAKCLI_TEACH_M1_SMOKE=1 (see scripts/e2e-teach-m1-smoke.sh).
Does not log session tokens, cookies, or passwords.
Plants known sentinel secrets so the orchestrator can substring-scan logs.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

from .teach_hub import TeachHubClient

# Env keys used to plant fake secrets for the headed log scan. Values must
# never be printed, returned in TEACH_M1_SMOKE_JSON, or written to hub events.
SENTINEL_ENV = {
    "token": "CLOAKCLI_TEACH_SMOKE_SENTINEL_TOKEN",
    "password": "CLOAKCLI_TEACH_SMOKE_SENTINEL_PASSWORD",
    "cookie": "CLOAKCLI_TEACH_SMOKE_SENTINEL_COOKIE",
}


def make_smoke_sentinels() -> dict[str, str]:
    """Per-run fake token/password/cookie values. Unique so source is not a hit."""
    nonce = uuid.uuid4().hex
    return {
        "token": f"m1sent_tok_{nonce}_NEVER_LOG",
        "password": f"m1sent_pw_{nonce}_NEVER_LOG",
        "cookie": f"m1sent_ck_{nonce}_NEVER_LOG",
    }


def sentinels_from_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for kind, key in SENTINEL_ENV.items():
        val = os.environ.get(key, "").strip()
        if val:
            out[kind] = val
    return out


def inject_sentinel_secrets(
    page: Any,
    allow_url: str,
    sentinels: dict[str, str] | None = None,
) -> bool:
    """Plant token/password/cookie sentinels into the headed session.

    Covers cookies, password/token fields, nested JS objects, and a URL-query
    fetch (`?token=&password=&cookie=`). Never logs the values.
    """
    sentinels = sentinels or sentinels_from_env()
    token = (sentinels.get("token") or "").strip()
    password = (sentinels.get("password") or "").strip()
    cookie = (sentinels.get("cookie") or "").strip()
    if not token or not password or not cookie:
        return False
    planted = 0
    try:
        page.context.add_cookies(
            [{"name": "m1_sentinel", "value": cookie, "url": allow_url}]
        )
        planted += 1
    except Exception:
        pass
    try:
        page.fill("#pw", password, timeout=5000)
        planted += 1
    except Exception:
        pass
    try:
        page.fill("#tok", token, timeout=5000)
        planted += 1
    except Exception:
        pass
    try:
        page.evaluate(
            """async (s) => {
                window.__m1Sentinel = {
                  nested: { token: s.token, password: s.password, cookie: s.cookie }
                };
                const q = new URLSearchParams({
                  token: s.token,
                  password: s.password,
                  cookie: s.cookie,
                });
                await fetch("/probe?" + q.toString(), { credentials: "include" });
                return true;
            }""",
            {"token": token, "password": password, "cookie": cookie},
        )
        planted += 1
    except Exception:
        pass
    return planted >= 4


def run_headed_smoke(ctx: Any, page: Any, allow_url: str | None, hub_client: TeachHubClient | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "worker_paired": False,
        "duplicate_pairing_rejected": False,
        "worker_reconnect_same_session": False,
        "injected_allow": False,
        "injected_deny": True,  # fail closed until proven false
        "sw_stopped": False,
        "injected_allow_after_sw": False,
        "sentinels_injected": False,
        "session_id": "",
        "error": "",
    }
    deny_url = os.environ.get("CLOAKCLI_TEACH_SMOKE_DENY_URL", "").strip()
    if not allow_url or not deny_url:
        result["error"] = "missing_allow_or_deny_url"
        return result
    if hub_client is None or not hub_client.paired.is_set() or not hub_client.session_id:
        result["error"] = "worker_not_paired"
        return result

    result["worker_paired"] = True
    session_id = str(hub_client.session_id)
    result["session_id"] = session_id

    result["duplicate_pairing_rejected"] = _duplicate_pairing_rejected(hub_client)
    result["worker_reconnect_same_session"] = _bounce_worker(hub_client, session_id)
    if hub_client.session_id:
        result["session_id"] = str(hub_client.session_id)

    try:
        sw = _wait_extension_sw(ctx, timeout=15.0)
        if sw is None:
            result["error"] = "extension_sw_missing"
            return result
        time.sleep(1.0)
        _goto(page, allow_url)
        result["injected_allow"] = _wait_injected(page, True, timeout=12.0)
        planted = inject_sentinel_secrets(page, allow_url)

        _goto(page, deny_url)
        # New document: injected flag must stay false (content.js never loaded).
        time.sleep(1.0)
        result["injected_deny"] = _wait_injected(page, True, timeout=1.5)

        result["sw_stopped"] = _stop_extension_service_worker(ctx, page)
        gone_deadline = time.time() + 4.0
        while time.time() < gone_deadline and _extension_workers(ctx):
            time.sleep(0.1)
        time.sleep(0.3)
        _goto(page, allow_url)
        if _wait_extension_sw(ctx, timeout=15.0) is None:
            result["error"] = "sw_did_not_restart"
            return result
        # SW is alive again; reload so inject/page_state run on this document.
        _goto(page, allow_url)
        result["injected_allow_after_sw"] = _wait_injected(page, True, timeout=12.0)
        planted_after = inject_sentinel_secrets(page, allow_url)
        result["sentinels_injected"] = bool(planted and planted_after)
        time.sleep(2.0)
    except Exception as e:
        result["error"] = type(e).__name__
        return result

    result["ok"] = bool(
        result["worker_paired"]
        and result["duplicate_pairing_rejected"]
        and result["worker_reconnect_same_session"]
        and result["injected_allow"]
        and not result["injected_deny"]
        and result["sw_stopped"]
        and result["injected_allow_after_sw"]
        and result["sentinels_injected"]
        and result["session_id"]
    )
    if not result["ok"] and not result["error"]:
        result["error"] = "checks_failed"
    return result


def _goto(page: Any, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)


def _wait_injected(page: Any, want: bool, timeout: float) -> bool:
    deadline = time.time() + timeout
    last = False
    while time.time() < deadline:
        try:
            last = bool(
                page.evaluate(
                    """() => document.documentElement.getAttribute("data-cloakcli-teach-injected") === "1"
                    || Boolean(window.__cloakcliTeachInjected)"""
                )
            )
        except Exception:
            last = False
        if last == want:
            return last
        time.sleep(0.2)
    return last


def _duplicate_pairing_rejected(hub_client: TeachHubClient) -> bool:
    other = TeachHubClient(
        hub_client.host,
        hub_client.port,
        hub_client.pairing_id,
        hub_client.pairing_code,
        role="worker",
    )
    t = threading.Thread(target=other.run, daemon=True)
    t.start()
    paired = other.wait_paired(2.0)
    err = other.last_error or ""
    other.stop()
    return (not paired) and err in ("pairing_consumed", "pairing_failed")


def _bounce_worker(hub_client: TeachHubClient, session_id: str) -> bool:
    hub_client.paired.clear()
    hub_client.drop_connection()
    if not hub_client.wait_paired(8.0):
        return False
    return hub_client.session_id == session_id and bool(hub_client.session_token)


def _wait_extension_sw(ctx: Any, timeout: float) -> Any | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = _extension_workers(ctx)
        if found:
            return found[0]
        time.sleep(0.2)
    return None


def _extension_workers(ctx: Any) -> list[Any]:
    out = []
    try:
        workers = list(ctx.service_workers)
    except Exception:
        return []
    for w in workers:
        url = ""
        try:
            url = w.url or ""
        except Exception:
            url = ""
        if "chrome-extension://" in url:
            out.append(w)
    return out


def _stop_extension_service_worker(ctx: Any, page: Any) -> bool:
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _extension_workers(ctx):
            break
        time.sleep(0.2)
    stopped = _stop_sw_cdp(ctx, page)
    if not stopped:
        stopped = _stop_sw_reload(ctx)
    deadline = time.time() + 4.0
    while time.time() < deadline:
        if not _extension_workers(ctx):
            return True
        time.sleep(0.1)
    return stopped


def _stop_sw_cdp(ctx: Any, page: Any) -> bool:
    try:
        session = ctx.new_cdp_session(page)
    except Exception:
        return False
    try:
        info = session.send("Target.getTargets")
    except Exception:
        return False
    n = 0
    for t in info.get("targetInfos") or []:
        url = str(t.get("url") or "")
        typ = str(t.get("type") or "")
        if "chrome-extension://" in url and "service_worker" in typ:
            try:
                session.send("Target.closeTarget", {"targetId": t.get("targetId")})
                n += 1
            except Exception:
                continue
    return n > 0


def _stop_sw_reload(ctx: Any) -> bool:
    """Last-resort: extension reload. Token is in chrome.storage.local too."""
    for w in _extension_workers(ctx):
        try:
            w.evaluate("() => chrome.runtime.reload()")
            return True
        except Exception:
            continue
    return False
