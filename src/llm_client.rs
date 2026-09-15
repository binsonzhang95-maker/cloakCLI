//! Shared OpenAI-compatible HTTP client (`GET {base}/models`, `POST chat/completions`).
//!
//! Timeouts, Bearer auth, body/count/id/pagination caps, strict `data[].id`
//! parse. Errors never include the API key or the raw response body.
//! Used by TEACH PATH (export optimize / assist) and by `llm models`.
//! Recover uses the Python worker client; do not mix the two call sites.

use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use crate::llm::{
    chat_completions_url, models_url, redact_secrets, MAX_MODELS, MAX_MODELS_BODY,
    MAX_MODELS_PAGES, MAX_MODEL_ID_LEN, MODELS_CONNECT_TIMEOUT_SEC, MODELS_READ_TIMEOUT_SEC,
};

/// Cap for a single chat/completions response body (teach optimize is one shot).
pub const MAX_CHAT_BODY: usize = 262_144;

#[derive(Debug, Clone, Default)]
pub struct ModelsList {
    pub ids: Vec<String>,
    pub truncated: bool,
    pub pages: u32,
    pub url: String,
}

pub fn fetch_models(base_url: &str, api_key: &str) -> Result<ModelsList> {
    let first = models_url(base_url)?;
    if api_key.is_empty() {
        bail!("API key is empty (set the env var named in api_key_env; never pass --api-key)");
    }

    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(MODELS_CONNECT_TIMEOUT_SEC))
        .timeout_read(Duration::from_secs(MODELS_READ_TIMEOUT_SEC))
        .timeout_write(Duration::from_secs(MODELS_CONNECT_TIMEOUT_SEC))
        .redirects(3)
        .user_agent("cloakcli/0.1")
        .build();

    let origin = origin_of(&first)?;
    let mut url = first.clone();
    let mut ids: Vec<String> = Vec::new();
    let mut truncated = false;
    let mut pages: u32 = 0;
    let mut seen_after: Vec<String> = Vec::new();

    loop {
        if pages >= MAX_MODELS_PAGES {
            truncated = true;
            break;
        }
        pages += 1;

        let (status, body, body_trunc) = get_models(&agent, &url, api_key)?;
        if body_trunc {
            truncated = true;
        }
        if !(200..300).contains(&status) {
            bail!("{}", public_http_error(status, Some(api_key)));
        }

        let parsed: Value = match serde_json::from_slice(&body) {
            Ok(v) => v,
            Err(_) => {
                if body_trunc {
                    bail!("GET /models response exceeded size cap (truncated, not JSON)");
                }
                bail!("GET /models returned non-JSON");
            }
        };

        let (page_ids, page_trunc, has_more, next) = parse_models_page(&parsed)?;
        if page_trunc {
            truncated = true;
        }
        for id in page_ids {
            let Ok(id) = crate::llm::accept_model_id(&id, Some(api_key)) else {
                continue;
            };
            if ids.len() >= MAX_MODELS {
                truncated = true;
                break;
            }
            if !ids.iter().any(|x| x == &id) {
                ids.push(id);
            }
        }
        if ids.len() >= MAX_MODELS {
            truncated = true;
            break;
        }
        if !has_more {
            break;
        }

        let next_url = match next.as_deref().filter(|n| !n.is_empty()) {
            Some(n) => resolve_next(&url, n, &origin)?,
            None => {
                let Some(last) = ids.last() else { break };
                if seen_after.iter().any(|x| x == last) {
                    break;
                }
                seen_after.push(last.clone());
                append_query(&first, "after", last)
            }
        };
        if next_url == url {
            truncated = true;
            break;
        }
        url = next_url;
    }

    if ids.is_empty() {
        bail!("GET /models returned no model ids (empty or unusable data[])");
    }

    Ok(ModelsList {
        ids,
        truncated,
        pages,
        url: first,
    })
}

/// One-shot chat/completions (no images). Shared by TEACH PATH optimize/assist.
#[derive(Debug, Clone, Default)]
pub struct ChatCompletion {
    pub text: String,
    pub tokens: u32,
}

pub fn chat_complete(
    base_url: &str,
    api_key: &str,
    model: &str,
    messages: &[Value],
    timeout_sec: u64,
) -> Result<ChatCompletion> {
    if api_key.is_empty() {
        bail!("API key is empty (set the env var named in api_key_env; never pass --api-key)");
    }
    let url = chat_completions_url(base_url)?;
    let model = crate::llm::accept_model_id(model, Some(api_key))?;
    if messages.is_empty() {
        bail!("chat/completions requires messages");
    }

    let payload = json!({
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 1024,
    });
    let body = serde_json::to_vec(&payload).context("serialize chat payload")?;

    let read_timeout = timeout_sec.clamp(5, 60);
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(MODELS_CONNECT_TIMEOUT_SEC))
        .timeout_read(Duration::from_secs(read_timeout))
        .timeout_write(Duration::from_secs(MODELS_CONNECT_TIMEOUT_SEC))
        .redirects(3)
        .user_agent("cloakcli/0.1")
        .build();

    let req = agent
        .post(&url)
        .set("Authorization", &format!("Bearer {api_key}"))
        .set("Content-Type", "application/json")
        .set("Accept", "application/json")
        .set("Connection", "close");

    let (status, raw, trunc) = match req.send_bytes(&body) {
        Ok(resp) => {
            let status = resp.status();
            let (raw, trunc) = read_capped_n(resp.into_reader(), MAX_CHAT_BODY)?;
            (status, raw, trunc)
        }
        Err(ureq::Error::Status(code, resp)) => {
            let _ = read_capped_n(resp.into_reader(), 400);
            (code, Vec::new(), false)
        }
        Err(ureq::Error::Transport(t)) => {
            let msg = redact_secrets(
                &format!("POST chat/completions network error: {t}"),
                Some(api_key),
            );
            bail!("{msg}")
        }
    };

    if !(200..300).contains(&status) {
        bail!(
            "{}",
            redact_secrets(
                &format!("POST chat/completions HTTP {status}"),
                Some(api_key)
            )
        );
    }
    if trunc {
        bail!("POST chat/completions response exceeded size cap");
    }
    let parsed: Value = match serde_json::from_slice(&raw) {
        Ok(v) => v,
        Err(_) => bail!("POST chat/completions returned non-JSON"),
    };
    let obj = parsed
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("POST chat/completions returned non-object JSON"))?;
    if let Some(err) = obj.get("error") {
        bail!(
            "{}",
            redact_secrets(&format!("chat/completions error: {err}"), Some(api_key))
        );
    }
    let text = extract_assistant_text(&parsed).unwrap_or_default();
    if text.trim().is_empty() {
        bail!("POST chat/completions returned empty content");
    }
    let tokens = obj
        .get("usage")
        .and_then(|u| u.get("total_tokens"))
        .and_then(|t| t.as_u64())
        .unwrap_or(0) as u32;
    Ok(ChatCompletion { text, tokens })
}

/// Streaming chat/completions (`stream: true`, SSE `data:` lines).
///
/// `on_delta` is invoked with each non-empty **content** chunk. Provider-exposed
/// reasoning/summary (never fabricated, never encrypted hidden chains) is
/// forwarded to `on_reasoning` when present. `cancel` is checked after every
/// delta and between reads. Errors never include the API key.
pub fn chat_complete_stream(
    base_url: &str,
    api_key: &str,
    model: &str,
    messages: &[Value],
    timeout_sec: u64,
    cancel: &AtomicBool,
    on_delta: impl FnMut(&str),
) -> Result<ChatCompletion> {
    chat_complete_stream_with_reasoning(
        base_url,
        api_key,
        model,
        messages,
        timeout_sec,
        cancel,
        on_delta,
        |_| {},
    )
}

/// Like [`chat_complete_stream`], plus `on_reasoning` for provider-exposed
/// reasoning/summary fields (`reasoning_content`, string `reasoning`,
/// `reasoning_summary`, `reasoning.summary`). Encrypted/hidden chains are skipped.
pub fn chat_complete_stream_with_reasoning(
    base_url: &str,
    api_key: &str,
    model: &str,
    messages: &[Value],
    timeout_sec: u64,
    cancel: &AtomicBool,
    mut on_delta: impl FnMut(&str),
    mut on_reasoning: impl FnMut(&str),
) -> Result<ChatCompletion> {
    if cancel.load(Ordering::SeqCst) {
        bail!("cancelled");
    }
    if api_key.is_empty() {
        bail!("API key is empty (set the env var named in api_key_env; never pass --api-key)");
    }
    let url = chat_completions_url(base_url)?;
    let model = crate::llm::accept_model_id(model, Some(api_key))?;
    if messages.is_empty() {
        bail!("chat/completions requires messages");
    }

    let payload = json!({
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 1024,
        "stream": true,
    });
    let body = serde_json::to_vec(&payload).context("serialize chat payload")?;

    let read_timeout = timeout_sec.clamp(5, 60);
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(MODELS_CONNECT_TIMEOUT_SEC))
        .timeout_read(Duration::from_secs(read_timeout))
        .timeout_write(Duration::from_secs(MODELS_CONNECT_TIMEOUT_SEC))
        .redirects(3)
        .user_agent("cloakcli/0.1")
        .build();

    let req = agent
        .post(&url)
        .set("Authorization", &format!("Bearer {api_key}"))
        .set("Content-Type", "application/json")
        .set("Accept", "text/event-stream")
        .set("Connection", "close");

    let resp = match req.send_bytes(&body) {
        Ok(resp) => resp,
        Err(ureq::Error::Status(code, resp)) => {
            let _ = read_capped_n(resp.into_reader(), 400);
            bail!(
                "{}",
                redact_secrets(
                    &format!("POST chat/completions HTTP {code}"),
                    Some(api_key)
                )
            )
        }
        Err(ureq::Error::Transport(t)) => {
            let msg = redact_secrets(
                &format!("POST chat/completions network error: {t}"),
                Some(api_key),
            );
            bail!("{msg}")
        }
    };

    let status = resp.status();
    if !(200..300).contains(&status) {
        let _ = read_capped_n(resp.into_reader(), 400);
        bail!(
            "{}",
            redact_secrets(
                &format!("POST chat/completions HTTP {status}"),
                Some(api_key)
            )
        );
    }

    let deadline = Instant::now() + Duration::from_secs(read_timeout);
    let reader = BufReader::new(resp.into_reader());
    read_sse_completion(
        reader,
        cancel,
        deadline,
        api_key,
        &mut on_delta,
        &mut on_reasoning,
    )
}

fn read_sse_completion<R: Read>(
    reader: BufReader<R>,
    cancel: &AtomicBool,
    deadline: Instant,
    api_key: &str,
    on_delta: &mut dyn FnMut(&str),
    on_reasoning: &mut dyn FnMut(&str),
) -> Result<ChatCompletion> {
    let mut reader = reader;
    let mut acc = String::new();
    let mut line = String::new();
    let mut total = 0usize;
    let mut saw_sse = false;
    let mut raw_json = String::new();

    loop {
        if cancel.load(Ordering::SeqCst) {
            bail!("cancelled");
        }
        if Instant::now() > deadline {
            bail!("POST chat/completions stream timed out");
        }
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(n) => {
                total += n;
                if total > MAX_CHAT_BODY {
                    bail!("POST chat/completions response exceeded size cap");
                }
                let trimmed = line.trim_end();
                if trimmed.is_empty() {
                    continue;
                }
                if let Some(data) = trimmed.strip_prefix("data:") {
                    saw_sse = true;
                    let data = data.trim();
                    if data.is_empty() {
                        continue;
                    }
                    if data == "[DONE]" {
                        break;
                    }
                    let parsed: Value = match serde_json::from_str(data) {
                        Ok(v) => v,
                        Err(_) => continue,
                    };
                    if parsed.get("error").is_some() {
                        bail!(
                            "{}",
                            redact_secrets(
                                "chat/completions error: stream error object",
                                Some(api_key)
                            )
                        );
                    }
                    let piece = extract_stream_piece(&parsed);
                    if let Some(r) = piece.reasoning {
                        on_reasoning(&r);
                        if cancel.load(Ordering::SeqCst) {
                            bail!("cancelled");
                        }
                    }
                    if let Some(delta) = piece.content {
                        acc.push_str(&delta);
                        on_delta(&delta);
                        if cancel.load(Ordering::SeqCst) {
                            bail!("cancelled");
                        }
                    }
                } else if !saw_sse {
                    raw_json.push_str(trimmed);
                    raw_json.push('\n');
                }
            }
            Err(e) => {
                let msg = redact_secrets(
                    &format!("POST chat/completions stream read: {e}"),
                    Some(api_key),
                );
                bail!("{msg}")
            }
        }
    }

    if acc.trim().is_empty() && !raw_json.trim().is_empty() {
        let parsed: Value = match serde_json::from_str(raw_json.trim()) {
            Ok(v) => v,
            Err(_) => bail!("POST chat/completions returned non-JSON (non-SSE)"),
        };
        if parsed.get("error").is_some() {
            bail!(
                "{}",
                redact_secrets("chat/completions error: response error object", Some(api_key))
            );
        }
        if let Some(r) = extract_exposed_reasoning_from_choice(&parsed) {
            on_reasoning(&r);
        }
        if let Some(text) = extract_assistant_text(&parsed) {
            if !text.is_empty() {
                on_delta(&text);
                acc = text;
            }
        }
    }

    if acc.trim().is_empty() {
        bail!("POST chat/completions returned empty content");
    }
    Ok(ChatCompletion {
        text: acc,
        tokens: 0,
    })
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
struct StreamPiece {
    content: Option<String>,
    reasoning: Option<String>,
}

fn extract_stream_delta(parsed: &Value) -> Option<String> {
    extract_stream_piece(parsed).content
}

fn extract_stream_piece(parsed: &Value) -> StreamPiece {
    let Some(choice) = parsed
        .get("choices")
        .and_then(|c| c.as_array())
        .and_then(|a| a.first())
    else {
        return StreamPiece::default();
    };
    let delta = choice
        .get("delta")
        .or_else(|| choice.get("message"))
        .cloned()
        .unwrap_or(Value::Null);
    StreamPiece {
        content: delta.get("content").and_then(content_to_nonempty_string),
        reasoning: extract_exposed_reasoning(&delta),
    }
}

fn extract_exposed_reasoning_from_choice(parsed: &Value) -> Option<String> {
    let choice = parsed.get("choices")?.as_array()?.first()?;
    let msg = choice.get("message").or_else(|| choice.get("delta"))?;
    extract_exposed_reasoning(msg)
}

/// Provider-exposed reasoning/summary only. Never encrypted/hidden chains,
/// never fabricated from content.
fn extract_exposed_reasoning(delta: &Value) -> Option<String> {
    for key in ["reasoning_content", "reasoning_summary"] {
        if let Some(s) = delta.get(key).and_then(content_to_nonempty_string) {
            return Some(s);
        }
    }
    match delta.get("reasoning") {
        Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
        Some(Value::Object(map)) => map.get("summary").and_then(content_to_nonempty_string),
        _ => {
            // Some OpenAI-compat proxies put a display string in `thinking`.
            delta.get("thinking").and_then(content_to_nonempty_string)
        }
    }
}

fn content_to_nonempty_string(content: &Value) -> Option<String> {
    match content {
        Value::String(s) if !s.is_empty() => Some(s.clone()),
        Value::Array(parts) => {
            let mut out = String::new();
            for p in parts {
                if let Some(t) = p.get("text").and_then(|x| x.as_str()) {
                    out.push_str(t);
                } else if let Some(s) = p.as_str() {
                    out.push_str(s);
                }
            }
            if out.is_empty() {
                None
            } else {
                Some(out)
            }
        }
        _ => None,
    }
}

fn extract_assistant_text(parsed: &Value) -> Option<String> {
    let choice = parsed.get("choices")?.as_array()?.first()?;
    let content = choice.get("message")?.get("content")?;
    match content {
        Value::String(s) => Some(s.clone()),
        Value::Array(parts) => {
            let mut out = String::new();
            for p in parts {
                if let Some(t) = p.get("text").and_then(|x| x.as_str()) {
                    out.push_str(t);
                } else if let Some(s) = p.as_str() {
                    out.push_str(s);
                }
            }
            Some(out)
        }
        _ => None,
    }
}

fn read_capped_n<R: Read>(mut r: R, cap: usize) -> Result<(Vec<u8>, bool)> {
    let mut buf = Vec::new();
    let mut tmp = [0u8; 8192];
    let mut truncated = false;
    loop {
        let n = r.read(&mut tmp).context("read HTTP body")?;
        if n == 0 {
            break;
        }
        if buf.len() >= cap {
            truncated = true;
            break;
        }
        let room = cap - buf.len();
        if n > room {
            buf.extend_from_slice(&tmp[..room]);
            truncated = true;
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
    }
    Ok((buf, truncated))
}

fn get_models(agent: &ureq::Agent, url: &str, api_key: &str) -> Result<(u16, Vec<u8>, bool)> {
    let req = agent
        .get(url)
        .set("Authorization", &format!("Bearer {api_key}"))
        .set("Accept", "application/json")
        .set("Connection", "close");
    match req.call() {
        Ok(resp) => {
            let status = resp.status();
            let (body, trunc) = read_capped(resp.into_reader())?;
            Ok((status, body, trunc))
        }
        Err(ureq::Error::Status(code, resp)) => {
            // Drain a tiny amount so the connection can close; never keep/log body.
            let _ = read_capped(resp.into_reader());
            Ok((code, Vec::new(), false))
        }
        Err(ureq::Error::Transport(t)) => {
            let msg = redact_secrets(&format!("GET /models network error: {t}"), Some(api_key));
            bail!("{msg}")
        }
    }
}

fn read_capped<R: Read>(r: R) -> Result<(Vec<u8>, bool)> {
    read_capped_n(r, MAX_MODELS_BODY)
}

/// Strict parse: object with `data` array; each element an object with string `id`.
pub fn parse_models_page(v: &Value) -> Result<(Vec<String>, bool, bool, Option<String>)> {
    let obj = v
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("GET /models returned non-object JSON"))?;
    let data = obj.get("data").and_then(|d| d.as_array()).ok_or_else(|| {
        anyhow::anyhow!("GET /models JSON missing data[] array of objects with string id")
    })?;

    let mut ids = Vec::new();
    let mut truncated = false;
    for item in data {
        let Some(id) = item
            .as_object()
            .and_then(|o| o.get("id"))
            .and_then(|i| i.as_str())
        else {
            continue;
        };
        let id = id.trim();
        if id.is_empty() {
            continue;
        }
        if id.chars().any(|c| c.is_control()) {
            continue;
        }
        if id.len() > MAX_MODEL_ID_LEN {
            truncated = true;
            continue;
        }
        if ids.len() >= MAX_MODELS {
            truncated = true;
            break;
        }
        if !ids.iter().any(|x| x == id) {
            ids.push(id.to_string());
        }
    }

    let has_more = obj
        .get("has_more")
        .and_then(|h| h.as_bool())
        .unwrap_or(false);
    let next = obj
        .get("next")
        .and_then(|n| n.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());

    Ok((ids, truncated, has_more, next))
}

pub fn public_http_error(status: u16, extra_key: Option<&str>) -> String {
    let msg = match status {
        401 | 403 => {
            "GET /models HTTP {status} (check api_key_env is set and the key is valid)".replace(
                "{status}",
                &status.to_string(),
            )
        }
        404 => "GET /models HTTP 404 (check base_url: http(s), single /v1, not /v1/v1)".into(),
        408 | 429 => format!("GET /models HTTP {status} (timeout or rate limit)"),
        s if (500..600).contains(&s) => format!("GET /models HTTP {s} (provider error)"),
        s => format!("GET /models HTTP {s}"),
    };
    redact_secrets(&msg, extra_key)
}

fn origin_of(url: &str) -> Result<String> {
    let u = crate::llm::parse_http_url(url, true)?;
    let host = u
        .host_str()
        .ok_or_else(|| anyhow::anyhow!("GET /models URL missing host"))?;
    let hostport = match u.port() {
        Some(p) if host.contains(':') => format!("[{host}]:{p}"),
        Some(p) => format!("{host}:{p}"),
        None if host.contains(':') => format!("[{host}]"),
        None => host.to_string(),
    };
    Ok(format!("{}://{hostport}", u.scheme()).to_ascii_lowercase())
}

fn resolve_next(current: &str, next: &str, origin: &str) -> Result<String> {
    let cur = crate::llm::parse_http_url(current, true)?;
    let joined = if next.starts_with("http://") || next.starts_with("https://") {
        crate::llm::parse_http_url(next, true)?
    } else {
        cur.join(next)
            .map_err(|_| anyhow::anyhow!("GET /models pagination next URL invalid"))?
    };
    let candidate = crate::llm::parse_http_url(joined.as_str(), true)?;
    let cand_origin = origin_of(candidate.as_str())?;
    if cand_origin != *origin {
        bail!("GET /models pagination next URL is off-origin (rejected)");
    }
    Ok(candidate.as_str().to_string())
}

fn append_query(url: &str, key: &str, value: &str) -> String {
    let enc = urlencode_query(value);
    if url.contains('?') {
        format!("{url}&{key}={enc}")
    } else {
        format!("{url}?{key}={enc}")
    }
}

fn urlencode_query(s: &str) -> String {
    let mut out = String::new();
    for b in s.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::llm::{MAX_MODELS, MAX_MODELS_BODY, MAX_MODELS_PAGES, MAX_MODEL_ID_LEN};
    use serde_json::json;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::{Duration, Instant};

    fn parse_http_request(stream: &mut dyn Read) -> (String, String, Vec<(String, String)>) {
        let mut buf = Vec::new();
        let mut tmp = [0u8; 1024];
        loop {
            match stream.read(&mut tmp) {
                Ok(0) => break,
                Ok(n) => {
                    buf.extend_from_slice(&tmp[..n]);
                    if buf.windows(4).any(|w| w == b"\r\n\r\n") {
                        break;
                    }
                    if buf.len() > 64_000 {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
        let text = String::from_utf8_lossy(&buf);
        let mut lines = text.split("\r\n");
        let req = lines.next().unwrap_or("");
        let mut sp = req.split_whitespace();
        let method = sp.next().unwrap_or("").to_string();
        let path = sp.next().unwrap_or("").to_string();
        let mut headers = Vec::new();
        for line in lines {
            if line.is_empty() {
                break;
            }
            if let Some((k, v)) = line.split_once(':') {
                headers.push((k.trim().to_ascii_lowercase(), v.trim().to_string()));
            }
        }
        (method, path, headers)
    }

    fn write_http(stream: &mut dyn Write, code: u16, body: &str) {
        let reason = if code == 200 { "OK" } else { "ERR" };
        let resp = format!(
            "HTTP/1.1 {code} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let _ = stream.write_all(resp.as_bytes());
        let _ = stream.flush();
    }

    /// Accept up to `n` requests, then exit (never block forever on extra accepts).
    fn spawn_n<F>(n: usize, handler: F) -> (String, Arc<Mutex<Vec<String>>>, thread::JoinHandle<()>)
    where
        F: Fn(usize, &str, &[(String, String)]) -> (u16, String) + Send + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).ok();
        let addr = listener.local_addr().unwrap();
        let auths = Arc::new(Mutex::new(Vec::new()));
        let auths_c = auths.clone();
        let join = thread::spawn(move || {
            use std::net::Shutdown;
            let deadline = std::time::Instant::now() + Duration::from_secs(6);
            let mut i = 0;
            while i < n && std::time::Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream.set_nonblocking(false).ok();
                        stream.set_read_timeout(Some(Duration::from_secs(2))).ok();
                        stream.set_write_timeout(Some(Duration::from_secs(2))).ok();
                        let (_m, path, headers) = parse_http_request(&mut stream);
                        if let Some((_, v)) = headers.iter().find(|(k, _)| k == "authorization") {
                            auths_c.lock().unwrap().push(v.clone());
                        }
                        let (code, body) = handler(i, &path, &headers);
                        write_http(&mut stream, code, &body);
                        let _ = stream.shutdown(Shutdown::Both);
                        i += 1;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        (format!("http://{addr}/v1"), auths, join)
    }

    #[test]
    fn fetch_sends_bearer_and_returns_ids() {
        let (base, auths, join) = spawn_n(1, |_i, path, _h| {
            assert!(path.starts_with("/v1/models"), "path={path}");
            (
                200,
                json!({"object":"list","data":[{"id":"gpt-4o"},{"id":"gpt-4o-mini","object":"model"}]})
                    .to_string(),
            )
        });
        let list = fetch_models(&base, "sk-test-secret-key-abc").unwrap();
        let _ = join.join();
        assert_eq!(list.ids, vec!["gpt-4o", "gpt-4o-mini"]);
        assert!(!list.truncated);
        let a = auths.lock().unwrap();
        assert_eq!(a.len(), 1);
        assert_eq!(a[0], "Bearer sk-test-secret-key-abc");
    }

    #[test]
    fn fetch_truncates_model_count() {
        let mut data = Vec::new();
        for i in 0..(MAX_MODELS + 40) {
            data.push(json!({"id": format!("m{i:04}")}));
        }
        let body = json!({"data": data}).to_string();
        let (base, _auths, join) = spawn_n(1, move |_i, _p, _h| (200, body.clone()));
        let list = fetch_models(&base, "k").unwrap();
        let _ = join.join();
        assert_eq!(list.ids.len(), MAX_MODELS);
        assert!(list.truncated);
        assert!(list.ids[0].starts_with('m'));
        // Must not keep going forever / include extras
        assert!(!list.ids.iter().any(|id| id == "m0296"));
    }

    #[test]
    fn fetch_rejects_bad_json_without_body() {
        let (base, _a, join) = spawn_n(1, |_i, _p, _h| (200, "NOT JSON {".into()));
        let err = fetch_models(&base, "sk-leaky-secret-xyz").unwrap_err().to_string();
        let _ = join.join();
        assert!(err.contains("non-JSON"), "{err}");
        assert!(!err.contains("sk-leaky-secret-xyz"), "{err}");
        assert!(!err.contains("NOT JSON"), "{err}");
    }

    #[test]
    fn fetch_rejects_non_object_and_missing_data() {
        let (base, _a, join) = spawn_n(1, |_i, _p, _h| (200, json!(["gpt-4o"]).to_string()));
        let err = fetch_models(&base, "k").unwrap_err().to_string();
        let _ = join.join();
        assert!(err.contains("non-object") || err.contains("data[]"), "{err}");
    }

    #[test]
    fn fetch_http_error_redacts_key_and_body() {
        let (base, _a, join) = spawn_n(1, |_i, _p, _h| {
            (401, json!({"error":"invalid_api_key sk-leaky-secret-xyz"}).to_string())
        });
        let err = fetch_models(&base, "sk-leaky-secret-xyz")
            .unwrap_err()
            .to_string();
        let _ = join.join();
        assert!(err.contains("401"), "{err}");
        assert!(!err.contains("sk-leaky-secret-xyz"), "{err}");
        assert!(!err.contains("invalid_api_key"), "{err}");
    }

    #[test]
    fn fetch_skips_non_string_ids_and_overlong() {
        let long = "x".repeat(MAX_MODEL_ID_LEN + 8);
        let (base, _a, join) = spawn_n(1, move |_i, _p, _h| {
            (
                200,
                json!({
                    "data": [
                        {"id": 1},
                        {"name": "nope"},
                        "gpt-raw",
                        {"id": ""},
                        {"id": long},
                        {"id": "ok-model"}
                    ]
                })
                .to_string(),
            )
        });
        let list = fetch_models(&base, "k").unwrap();
        let _ = join.join();
        assert_eq!(list.ids, vec!["ok-model"]);
        assert!(list.truncated);
    }

    #[test]
    fn fetch_pagination_cap_stops_infinite_next() {
        let (base, auths, join) = spawn_n(MAX_MODELS_PAGES as usize, |i, _path, _h| {
            (
                200,
                json!({
                    "data": [{"id": format!("p{i}")}],
                    "has_more": true,
                    "next": format!("/v1/models?cursor={i}")
                })
                .to_string(),
            )
        });
        let list = fetch_models(&base, "k").unwrap();
        let _ = join.join();
        assert!(list.truncated);
        assert!(list.pages <= MAX_MODELS_PAGES);
        assert!(!auths.lock().unwrap().is_empty());
        assert!(list.ids.len() <= MAX_MODELS_PAGES as usize);
    }

    #[test]
    fn fetch_rejects_file_url() {
        let err = fetch_models("file:///etc/passwd", "k").unwrap_err().to_string();
        assert!(
            err.contains("rejects") || err.to_ascii_lowercase().contains("http"),
            "{err}"
        );
        assert!(!err.contains("sk-"));
    }

    #[test]
    fn parse_requires_data_array() {
        let err = parse_models_page(&json!({"models":[{"id":"x"}]})).unwrap_err();
        assert!(err.to_string().contains("data[]"));
    }

    #[test]
    fn oversized_body_does_not_leak() {
        let huge = format!("{{\"data\":[\"{}", "A".repeat(MAX_MODELS_BODY + 100));
        let (base, _a, join) = spawn_n(1, move |_i, _p, _h| (200, huge.clone()));
        let err = fetch_models(&base, "sk-body-secret").unwrap_err().to_string();
        let _ = join.join();
        assert!(
            err.contains("size cap") || err.contains("non-JSON"),
            "{err}"
        );
        assert!(!err.contains("sk-body-secret"), "{err}");
        assert!(!err.contains(&"A".repeat(50)), "{err}");
    }

    #[test]
    fn fetch_rejects_ids_equal_to_api_key_or_control_chars() {
        let key = "sk-id-secret-xyz";
        let (base, _a, join) = spawn_n(1, move |_i, _p, _h| {
            (
                200,
                json!({
                    "data": [
                        {"id": key},
                        {"id": format!("pre-{key}")},
                        {"id": "ok-model"},
                        {"id": "bad\u{0007}id"}
                    ]
                })
                .to_string(),
            )
        });
        let list = fetch_models(&base, key).unwrap();
        let _ = join.join();
        assert_eq!(list.ids, vec!["ok-model"]);
        let shown = crate::llm::format_models_list(&list);
        assert!(!shown.contains(key), "{shown}");
        assert!(!shown.contains('\u{0007}'));
    }

    #[test]
    fn fetch_empty_after_rejecting_key_ids_does_not_echo() {
        let key = "sk-only-secret-xyz";
        let (base, _a, join) = spawn_n(1, move |_i, _p, _h| {
            (200, json!({"data":[{"id": key}]}).to_string())
        });
        let err = fetch_models(&base, key).unwrap_err().to_string();
        let _ = join.join();
        assert!(err.contains("no model ids") || err.contains("empty"), "{err}");
        assert!(!err.contains(key), "{err}");
    }

    #[test]
    fn chat_complete_returns_text_and_tokens() {
        let (base, auths, join) = spawn_n(1, |_i, path, _h| {
            assert!(path.contains("chat/completions"), "path={path}");
            (
                200,
                json!({
                    "choices":[{"message":{"content":"{\"ok\":true}"}}],
                    "usage":{"total_tokens": 12}
                })
                .to_string(),
            )
        });
        let out = chat_complete(
            &base,
            "sk-chat-secret",
            "gpt-test",
            &[json!({"role":"user","content":"ping"})],
            8,
        )
        .unwrap();
        let _ = join.join();
        assert_eq!(out.text, "{\"ok\":true}");
        assert_eq!(out.tokens, 12);
        assert_eq!(auths.lock().unwrap()[0], "Bearer sk-chat-secret");
    }

    #[test]
    fn chat_complete_http_error_redacts_key() {
        let (base, _a, join) = spawn_n(1, |_i, _p, _h| {
            (401, json!({"error":"invalid_api_key sk-chat-leak"}).to_string())
        });
        let err = chat_complete(
            &base,
            "sk-chat-leak",
            "m",
            &[json!({"role":"user","content":"x"})],
            8,
        )
        .unwrap_err()
        .to_string();
        let _ = join.join();
        assert!(err.contains("401"), "{err}");
        assert!(!err.contains("sk-chat-leak"), "{err}");
    }

    fn write_sse(stream: &mut dyn Write, chunks: &[&str]) {
        let _ = stream.write_all(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n",
        );
        for c in chunks {
            let payload = json!({"choices":[{"delta":{"content": c}}]});
            let _ = write!(stream, "data: {payload}\n\n");
            let _ = stream.flush();
            thread::sleep(Duration::from_millis(8));
        }
        let _ = stream.write_all(b"data: [DONE]\n\n");
        let _ = stream.flush();
    }

    fn spawn_sse(
        chunks: &'static [&'static str],
    ) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).ok();
        let addr = listener.local_addr().unwrap();
        let join = thread::spawn(move || {
            use std::net::Shutdown;
            let deadline = Instant::now() + Duration::from_secs(6);
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream.set_nonblocking(false).ok();
                        stream.set_read_timeout(Some(Duration::from_secs(2))).ok();
                        stream.set_write_timeout(Some(Duration::from_secs(2))).ok();
                        let _ = parse_http_request(&mut stream);
                        write_sse(&mut stream, chunks);
                        let _ = stream.shutdown(Shutdown::Both);
                        break;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        if Instant::now() > deadline {
                            break;
                        }
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        (format!("http://127.0.0.1:{}/v1", addr.port()), join)
    }

    #[test]
    fn chat_complete_stream_emits_deltas() {
        let (base, join) = spawn_sse(&["Hel", "lo", "!"]);
        let mut parts = Vec::new();
        let cancel = AtomicBool::new(false);
        let out = chat_complete_stream(
            &base,
            "sk-stream-secret",
            "gpt-test",
            &[json!({"role":"user","content":"ping"})],
            8,
            &cancel,
            |d| parts.push(d.to_string()),
        )
        .unwrap();
        let _ = join.join();
        assert_eq!(parts, vec!["Hel", "lo", "!"]);
        assert_eq!(out.text, "Hello!");
        assert!(parts.len() >= 2, "expected incremental deltas, got {parts:?}");
    }

    #[test]
    fn chat_complete_stream_cancel_after_first_delta() {
        let (base, join) = spawn_sse(&["aa", "bb", "cc", "dd"]);
        let mut parts = Vec::new();
        let cancel = AtomicBool::new(false);
        let err = chat_complete_stream(
            &base,
            "sk-stream-secret",
            "gpt-test",
            &[json!({"role":"user","content":"ping"})],
            8,
            &cancel,
            |d| {
                parts.push(d.to_string());
                if parts.len() == 1 {
                    cancel.store(true, Ordering::SeqCst);
                }
            },
        )
        .unwrap_err()
        .to_string();
        let _ = join.join();
        assert!(err.contains("cancelled"), "{err}");
        assert!(!err.contains("sk-stream-secret"), "{err}");
        assert_eq!(parts, vec!["aa"]);
    }

    fn write_sse_mixed(stream: &mut dyn Write, parts: &[(&str, &str)]) {
        let _ = stream.write_all(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n",
        );
        for (field, c) in parts {
            let payload = match *field {
                "reasoning_content" => json!({"choices":[{"delta":{"reasoning_content": c}}]}),
                "reasoning" => json!({"choices":[{"delta":{"reasoning": c}}]}),
                "reasoning_summary" => json!({"choices":[{"delta":{"reasoning_summary": c}}]}),
                "encrypted" => json!({"choices":[{"delta":{"reasoning":{"encrypted_content": c}}}]}),
                _ => json!({"choices":[{"delta":{"content": c}}]}),
            };
            let _ = write!(stream, "data: {payload}\n\n");
            let _ = stream.flush();
            thread::sleep(Duration::from_millis(8));
        }
        let _ = stream.write_all(b"data: [DONE]\n\n");
        let _ = stream.flush();
    }

    fn spawn_sse_mixed(
        parts: &'static [(&'static str, &'static str)],
    ) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).ok();
        let addr = listener.local_addr().unwrap();
        let join = thread::spawn(move || {
            use std::net::Shutdown;
            let deadline = Instant::now() + Duration::from_secs(6);
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream.set_nonblocking(false).ok();
                        stream.set_read_timeout(Some(Duration::from_secs(2))).ok();
                        stream.set_write_timeout(Some(Duration::from_secs(2))).ok();
                        let _ = parse_http_request(&mut stream);
                        write_sse_mixed(&mut stream, parts);
                        let _ = stream.shutdown(Shutdown::Both);
                        break;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        if Instant::now() > deadline {
                            break;
                        }
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        (format!("http://127.0.0.1:{}/v1", addr.port()), join)
    }

    #[test]
    fn extract_stream_piece_reasoning_not_content() {
        let v = json!({"choices":[{"delta":{"reasoning_content":"step 1","content":null}}]});
        let p = extract_stream_piece(&v);
        assert_eq!(p.reasoning.as_deref(), Some("step 1"));
        assert!(p.content.is_none());

        let v = json!({"choices":[{"delta":{"reasoning":"summary A","content":"Hi"}}]});
        let p = extract_stream_piece(&v);
        assert_eq!(p.reasoning.as_deref(), Some("summary A"));
        assert_eq!(p.content.as_deref(), Some("Hi"));

        let v = json!({"choices":[{"delta":{"reasoning":{"summary":"ok"},"content":"x"}}]});
        let p = extract_stream_piece(&v);
        assert_eq!(p.reasoning.as_deref(), Some("ok"));
        assert_eq!(p.content.as_deref(), Some("x"));
    }

    #[test]
    fn extract_skips_encrypted_hidden_reasoning() {
        let v = json!({"choices":[{"delta":{"reasoning":{"encrypted_content":"HIDDEN_CHAIN"}}}]});
        let p = extract_stream_piece(&v);
        assert!(p.reasoning.is_none(), "{p:?}");
        assert!(p.content.is_none());
    }

    #[test]
    fn extract_no_reasoning_does_not_fabricate() {
        let v = json!({"choices":[{"delta":{"content":"just answer"}}]});
        let p = extract_stream_piece(&v);
        assert!(p.reasoning.is_none());
        assert_eq!(p.content.as_deref(), Some("just answer"));
        assert!(extract_stream_delta(&v).as_deref() == Some("just answer"));
    }

    #[test]
    fn chat_complete_stream_forwards_reasoning_separately() {
        let (base, join) = spawn_sse_mixed(&[
            ("reasoning_content", "think "),
            ("reasoning_content", "first"),
            ("content", "ANS"),
        ]);
        let mut parts = Vec::new();
        let mut reason = Vec::new();
        let cancel = AtomicBool::new(false);
        let out = chat_complete_stream_with_reasoning(
            &base,
            "sk-stream-secret",
            "gpt-test",
            &[json!({"role":"user","content":"ping"})],
            8,
            &cancel,
            |d| parts.push(d.to_string()),
            |r| reason.push(r.to_string()),
        )
        .unwrap();
        let _ = join.join();
        assert_eq!(parts, vec!["ANS"]);
        assert_eq!(reason, vec!["think ", "first"]);
        assert_eq!(out.text, "ANS");
        assert!(!out.text.contains("think"), "reasoning must not mix into content");
    }

    #[test]
    fn chat_complete_stream_without_reasoning_does_not_callback() {
        let (base, join) = spawn_sse(&["Hel", "lo"]);
        let mut reason = Vec::new();
        let cancel = AtomicBool::new(false);
        let out = chat_complete_stream_with_reasoning(
            &base,
            "sk-stream-secret",
            "gpt-test",
            &[json!({"role":"user","content":"ping"})],
            8,
            &cancel,
            |_| {},
            |r| reason.push(r.to_string()),
        )
        .unwrap();
        let _ = join.join();
        assert!(reason.is_empty(), "no provider reasoning → no fabricated chain: {reason:?}");
        assert_eq!(out.text, "Hello");
    }

}
