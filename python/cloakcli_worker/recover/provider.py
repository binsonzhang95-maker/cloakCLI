"""Single OpenAI-compatible vision provider via chat/completions + image_url."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ..llm_config import LlmConfig
from ..redact import redact_text

# 1x1 transparent PNG for connectivity tests
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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
    ) -> tuple[str, int]:
        """Return (assistant_text, total_tokens). Never logs Authorization."""
        if not cfg.base_url or not cfg.model:
            raise ProviderError("llm config missing base_url or model")
        key = os.environ.get(cfg.api_key_env, "")
        if not key:
            raise ProviderError(f"env {cfg.api_key_env} is not set")

        url = cfg.base_url.rstrip("/") + "/chat/completions"
        parsed_scheme = url.split(":", 1)[0].lower()
        if parsed_scheme not in ("http", "https"):
            raise ProviderError("base_url must be http(s)")

        body_messages = list(messages)
        if image_b64 and body_messages:
            last = dict(body_messages[-1])
            content = last.get("content")
            if isinstance(content, str):
                last["content"] = [
                    {"type": "text", "text": content},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
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
                err_body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                err_body = ""
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


def test_llm(cfg: LlmConfig) -> dict[str, Any]:
    """Connectivity probe. Response never includes the API key."""
    key_present = bool(os.environ.get(cfg.api_key_env))
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
