// Secret redaction for Teach Chat mock storage / echo.
// Patterns: token / cookie / Authorization / bearer / api_key / sk- / proxy userinfo.
// Regexes are created per call so `lastIndex` cannot skip a later replace.

function labelRe() {
  return /(?:authorization|set-cookie|cookie|token|api[_-]?key|apikey|password|passwd|secret)\s*[:=]\s*(?:bearer\s+)?[^\s,;"']+/gi;
}
function bearerRe() {
  return /(?:^|[^A-Za-z0-9])bearer\s+[A-Za-z0-9._\-+/=]+/gi;
}
function jsonSecretRe() {
  return /("(?:session_token|access_token|refresh_token|id_token|token|authorization|cookie|api[_-]?key|apikey|password|secret)"\s*:\s*")[^"]*(")/gi;
}
function skRe() {
  return /\bsk-(?:proj-)?[A-Za-z0-9]{8,}\b/g;
}
function proxyRe() {
  return /(https?:\/\/)([^/@:\s]+):([^/@\s]+)@/gi;
}

export function looksLikeSecret(text) {
  const s = String(text || "");
  if (!s) return false;
  return (
    labelRe().test(s) ||
    bearerRe().test(s) ||
    jsonSecretRe().test(s) ||
    skRe().test(s) ||
    proxyRe().test(s)
  );
}

export function redactText(text) {
  if (typeof text !== "string" || !text) return text;
  let s = text;
  s = s.replace(proxyRe(), "$1***:***@");
  s = s.replace(jsonSecretRe(), "$1***$2");
  s = s.replace(labelRe(), (m) => {
    const sep = m.search(/[:=]/);
    if (sep < 0) return "***";
    return m.slice(0, sep + 1) + (m[sep + 1] === " " ? " " : "") + "***";
  });
  s = s.replace(bearerRe(), (m) =>
    m[0] === "b" || m[0] === "B" ? "Bearer ***" : `${m[0]}Bearer ***`,
  );
  s = s.replace(skRe(), "sk-***");
  return s;
}

export function summarizeUserMessage(text) {
  const raw = String(text || "");
  const chars = raw.length;
  const redacted = redactText(raw);
  const hadSecrets = looksLikeSecret(raw) || redacted !== raw;
  if (hadSecrets) {
    return {
      display: `[message recorded locally · ${chars} chars · secrets omitted]`,
      hadSecrets: true,
      chars,
    };
  }
  return { display: raw, hadSecrets: false, chars };
}

/** Mock teach_chat_event preview — never echoes raw input. */
export function mockAckPreview(summary) {
  return [
    "ack mock event",
    "kind: teach_chat_event",
    "status: recorded_locally",
    "preview: [omitted]",
    `chars: ${summary.chars}`,
    `secrets: ${summary.hadSecrets ? "omitted" : "none"}`,
  ].join("\n");
}

const EVENT_ID_KEYS = new Set([
  "kind",
  "v",
  "role",
  "code",
  "phase",
  "state",
  "job_id",
  "session_id",
  "pairing_id",
  "pairing_code",
  "hub_url",
  "profile",
  "spawn_browser",
  "hub",
  "worker",
  "extension",
  "busy",
  "ok",
  "mode",
  "last_request_id",
  "running",
  "seq",
  "done",
  "hub_resume",
]);

/** Defense in depth: redact free-text fields on teach_chat_event payloads. */
export function redactEventPayload(payload) {
  if (payload == null) return payload;
  if (typeof payload === "string") return redactText(payload);
  if (Array.isArray(payload)) return payload.map(redactEventPayload);
  if (typeof payload !== "object") return payload;
  const out = {};
  for (const [k, v] of Object.entries(payload)) {
    out[k] = EVENT_ID_KEYS.has(k) ? v : redactEventPayload(v);
  }
  return out;
}
