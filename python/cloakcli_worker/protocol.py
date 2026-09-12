"""JSONL request/response helpers."""

from __future__ import annotations

import json
import sys
from typing import Any


def read_request_line(line: str) -> dict[str, Any] | None:
    """Parse one JSONL request. Empty line → None (skip)."""
    line = line.strip()
    if not line:
        return None
    return json.loads(line)


def read_request() -> dict[str, Any] | None:
    """Read one request from stdin. EOF → None. Empty line → skip (return noop skip marker)."""
    line = sys.stdin.readline()
    if not line:
        return None  # EOF
    line = line.strip()
    if not line:
        return {"id": "0", "cmd": "_skip"}  # discard empty lines
    return json.loads(line)


def write_response(resp: dict[str, Any], out=None) -> None:
    stream = out if out is not None else sys.stdout
    stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
    stream.flush()


def ok(req_id: str, data: Any = None) -> dict[str, Any]:
    return {"id": req_id, "ok": True, "data": data if data is not None else {}}


def err(req_id: str, message: str) -> dict[str, Any]:
    return {"id": req_id, "ok": False, "error": message}
