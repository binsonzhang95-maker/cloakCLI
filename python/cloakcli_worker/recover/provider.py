"""Single OpenAI-compatible vision provider via chat/completions + image_url."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..llm_config import (
    LlmConfig,
    MAX_MODEL_ID_LEN,
    MAX_MODELS,
    MAX_MODELS_BODY,
    MAX_MODELS_PAGES,
    MODELS_CONNECT_TIMEOUT_SEC,
    MODELS_READ_TIMEOUT_SEC,
    accept_model_id,
    chat_completions_url,
    models_url,
    resolve_api_key,
)
from ..redact import redact_text

# 1x1 transparent PNG for connectivity tests
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}


def _normalize_image_mime(raw: str | None) -> str:
    s = (raw or "image/png").strip().lower()
    if s == "image/jpg":
        s = "image/jpeg"
    if s not in _ALLOWED_IMAGE_MIMES:
        return "image/png"
    return s


class ProviderError(RuntimeError):
    pass


class OpenAICompatProvider:
    def complete(
        self,
        cfg: LlmConfig,
        messages: list[dict[str, Any]],
        *,
        image_b64: str | None = None,
        timeout_sec: float = 60,
        image_mime: str | None = None,
    ) -> tuple[str, int]:
        """Return (assistant_text, total_tokens). Never logs Authorization."""
        if not cfg.base_url or not cfg.model:
            raise ProviderError("llm config missing base_url or model")
        key = resolve_api_key(cfg)
        if not key:
            raise ProviderError(f"env {cfg.api_key_env} is not set")

        url = chat_completions_url(cfg.base_url)
        if not url:
            raise ProviderError("base_url must be http(s)")

        mime = _normalize_image_mime(image_mime)
        body_messages = list(messages)
        if image_b64 and body_messages:
            last = dict(body_messages[-1])
            content = last.get("content")
            if isinstance(content, str):
                last["content"] = [
                    {"type": "text", "text": content},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ]
                body_messages[-1] = last

        payload = {
            "model": cfg.model,
            "messages": body_messages,
            "temperature": 0,
            "max_tokens": 1024,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "cloakcli-worker/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=max(5, float(timeout_sec))) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                raw, _trunc = _read_limited(e, 400)
                err_body = raw.decode("utf-8", errors="replace")[:400]
            except Exception:
                err_body = ""
            try:
                e.close()
            except Exception:
                pass
            msg = redact_text(
                f"HTTP {e.code} {e.reason} {err_body}", extra=[key]
            )
            raise ProviderError(msg) from None
        except urllib.error.URLError as e:
            raise ProviderError(redact_text(f"network: {e.reason}", extra=[key])) from None
        except TimeoutError:
            raise ProviderError("model request timed out") from None
        except Exception as e:
            raise ProviderError(redact_text(f"{type(e).__name__}: {e}", extra=[key])) from None

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ProviderError(f"model returned non-JSON: {e.msg}") from None

        if not isinstance(parsed, dict):
            raise ProviderError("model returned unexpected JSON")
        err = parsed.get("error")
        if err:
            raise ProviderError(redact_text(str(err), extra=[key]))

        choices = parsed.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text") or ""))
                    elif isinstance(p, str):
                        parts.append(p)
                text = "".join(parts)
        usage = parsed.get("usage") or {}
        tokens = 0
        try:
            tokens = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            tokens = 0
        if not text.strip():
            raise ProviderError("model returned empty content")
        return text, tokens


def list_models(cfg: LlmConfig, *, api_key: str | None = None) -> dict[str, Any]:
    """GET {base}/models — ids only. Never returns the key or raw JSON."""
    key = api_key if api_key is not None else resolve_api_key(cfg)
    url = models_url(cfg.base_url)
    out: dict[str, Any] = {
        "ok": False,
        "ids": [],
        "truncated": False,
        "pages": 0,
        "url": url,
    }
    if not url:
        out["error"] = "base_url must be http(s)"
        return out
    if not key:
        out["error"] = f"env {cfg.api_key_env} is not set"
        return out

    ids: list[str] = []
    truncated = False
    pages = 0
    current = url
    origin = _origin(url)
    seen_after: list[str] = []
    has_more = False
    timeout = max(MODELS_CONNECT_TIMEOUT_SEC, MODELS_READ_TIMEOUT_SEC)

    try:
        while pages < MAX_MODELS_PAGES:
            pages += 1
            req = urllib.request.Request(
                current,
                method="GET",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Accept": "application/json",
                    "User-Agent": "cloakcli-worker/0.1",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw, body_trunc = _read_limited(resp, MAX_MODELS_BODY)
            except urllib.error.HTTPError as e:
                try:
                    _read_limited(e, MAX_MODELS_BODY)
                except Exception:
                    pass
                try:
                    e.close()
                except Exception:
                    pass
                out["error"] = redact_text(
                    f"GET /models HTTP {e.code}", extra=[key]
                )
                return out
            except urllib.error.URLError as e:
                out["error"] = redact_text(f"GET /models network: {e.reason}", extra=[key])
                return out
            if body_trunc:
                truncated = True
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                out["error"] = (
                    "GET /models response exceeded size cap (truncated, not JSON)"
                    if body_trunc
                    else "GET /models returned non-JSON"
                )
                return out
            if not isinstance(parsed, dict):
                out["error"] = "GET /models returned non-object JSON"
                return out
            data = parsed.get("data")
            if not isinstance(data, list):
                out["error"] = "GET /models JSON missing data[] array of objects with string id"
                return out
            for item in data:
                if not isinstance(item, dict):
                    continue
                mid = item.get("id")
                if not isinstance(mid, str):
                    continue
                accepted = accept_model_id(mid, key)
                if accepted is None:
                    if isinstance(mid, str) and len(mid.strip()) > MAX_MODEL_ID_LEN:
                        truncated = True
                    continue
                if len(ids) >= MAX_MODELS:
                    truncated = True
                    break
                if accepted not in ids:
                    ids.append(accepted)
            if len(ids) >= MAX_MODELS:
                truncated = True
                break
            has_more = bool(parsed.get("has_more"))
            nxt = parsed.get("next")
            if not has_more:
                break
            if isinstance(nxt, str) and nxt.strip():
                nxt_url = _resolve_next(current, nxt.strip(), origin)
                if not nxt_url:
                    truncated = True
                    break
                current = nxt_url
            else:
                if not ids:
                    break
                last = ids[-1]
                if last in seen_after:
                    break
                seen_after.append(last)
                current = _append_query(url, "after", last)
        if pages >= MAX_MODELS_PAGES and has_more:
            truncated = True
    except Exception as e:
        out["error"] = redact_text(f"{type(e).__name__}: {e}", extra=[key])
        return out

    if not ids:
        out["error"] = "GET /models returned no model ids (empty or unusable data[])"
        return out
    out["ok"] = True
    out["ids"] = ids
    out["truncated"] = truncated
    out["pages"] = pages
    return out


def _read_limited(resp: Any, cap: int) -> tuple[bytes, bool]:
    """Bounded read. Never call read() without a size (unbounded)."""
    try:
        data = resp.read(cap + 1)
    except Exception:
        data = b""
    truncated = len(data) > cap
    if truncated:
        data = data[:cap]
    return data, truncated


def _origin(url: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(url)
    host = p.hostname or ""
    if not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{p.port}" if p.port else host
    return f"{p.scheme}://{netloc}".lower()


def _resolve_next(current: str, nxt: str, origin: str) -> str:
    from urllib.parse import urljoin, urlparse

    if nxt.startswith("http://") or nxt.startswith("https://"):
        cand = nxt
    else:
        cand = urljoin(current, nxt)
    p = urlparse(cand)
    if p.scheme not in ("http", "https"):
        return ""
    if p.username or p.password or "@" in (p.netloc or ""):
        return ""
    if not p.hostname:
        return ""
    if p.fragment:
        return ""
    cand_origin = _origin(cand)
    if not cand_origin or cand_origin != origin:
        return ""
    return cand


def _append_query(url: str, key: str, value: str) -> str:
    from urllib.parse import quote

    enc = quote(value, safe="-_.~")
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{key}={enc}"


def test_llm(cfg: LlmConfig) -> dict[str, Any]:
    """Connectivity probe. Response never includes the API key."""
    key_present = bool(resolve_api_key(cfg))
    out: dict[str, Any] = {
        "ok": False,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key_env": cfg.api_key_env,
        "key_present": key_present,
        "enabled": cfg.enabled,
    }
    if not cfg.enabled:
        out["error"] = "llm recover disabled"
        return out
    if not cfg.base_url or not cfg.model:
        out["error"] = "incomplete config (need base_url and model)"
        return out
    if not key_present:
        out["error"] = f"env {cfg.api_key_env} is not set"
        return out
    provider = OpenAICompatProvider()
    messages = [
        {
            "role": "system",
            "content": "Reply with a JSON object {\"ok\": true} and nothing else.",
        },
        {"role": "user", "content": "ping"},
    ]
    try:
        text, tokens = provider.complete(
            cfg, messages, image_b64=TINY_PNG_B64, timeout_sec=30
        )
    except ProviderError as e:
        out["error"] = redact_text(str(e))
        return out
    out["ok"] = True
    out["usage_tokens"] = tokens
    snippet = redact_text(text.strip().replace("\n", " ")[:80])
    out["reply_preview"] = snippet
    return out
