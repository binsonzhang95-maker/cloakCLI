#!/usr/bin/env python3
"""Hubstudio Local API client (http://127.0.0.1:6873). No secrets in logs.

Never start/stop protected container codes (pilot / live accounts).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

API = os.environ.get("HUBSTUDIO_API", "http://127.0.0.1:6873")

# Pilot / live accounts — never start or stop via this client.
PROTECTED_CONTAINER_CODES = frozenset({1863073221, 1863107506, 1863108423})
PROTECTED_REFUSED = {
    "code": -1,
    "msg": "protected_env_refused",
    "data": None,
}


def is_protected_container(container_code: int | str | None) -> bool:
    try:
        return int(container_code) in PROTECTED_CONTAINER_CODES
    except (TypeError, ValueError):
        return False


def api_post(path: str, body: dict | None = None, timeout: float = 120) -> dict[str, Any]:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"code": e.code, "msg": raw[:300], "data": None}


def scrub(d: Any) -> Any:
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            if any(x in k.lower() for x in ("pass", "proxy", "secret", "token", "cookie", "account")):
                out[k] = "***"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(d, list):
        return [scrub(x) for x in d]
    return d


def env_list(page: int = 1, size: int = 100) -> dict[str, Any]:
    return api_post("/api/v1/env/list", {"current": page, "size": size})


def find_env_by_name(name: str) -> dict[str, Any] | None:
    page = 1
    while page <= 20:
        d = env_list(page=page, size=100)
        lst = ((d.get("data") or {}).get("list")) or []
        for e in lst:
            if e.get("containerName") == name:
                return e
        if len(lst) < 100:
            break
        page += 1
    return None


def proxy_update(
    container_code: int,
    host: str,
    port: int,
    account: str,
    password: str,
    proxy_type_name: str = "HTTP",
    as_dynamic_type: int = 0,
) -> dict[str, Any]:
    if is_protected_container(container_code):
        return dict(PROTECTED_REFUSED)
    # Hub Local API requires asDynamicType (E010008 when omitted).
    body = {
        "containerCode": int(container_code),
        "proxyTypeName": proxy_type_name,
        "proxyHost": host,
        "proxyPort": int(port),
        "proxyAccount": account,
        "proxyPassword": password,
        "asDynamicType": int(as_dynamic_type),
    }
    return api_post("/api/v1/env/proxy/update", body)


def browser_start(container_code: int) -> dict[str, Any]:
    if is_protected_container(container_code):
        return dict(PROTECTED_REFUSED)
    return api_post("/api/v1/browser/start", {"containerCode": int(container_code)})


def browser_stop(container_code: int) -> dict[str, Any]:
    if is_protected_container(container_code):
        return dict(PROTECTED_REFUSED)
    return api_post("/api/v1/browser/stop", {"containerCode": int(container_code)})


def wait_cdp(port: int, timeout: float = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=2) as resp:
                resp.read()
            return True
        except Exception:
            time.sleep(0.5)
    return False
