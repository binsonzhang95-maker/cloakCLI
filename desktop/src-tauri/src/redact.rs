//! Secret redaction for desktop DTOs and error strings.
//!
//! The desktop crate has no path-dep on `cloakcli`, so this is a local copy of
//! the same rules: token / Authorization / cookie / bearer / api_key / sk- /
//! proxy userinfo. No `regex` crate.

/// Proxy URL with userinfo replaced by `***:***`. Never the raw value.
pub fn redact_proxy(proxy: &str) -> String {
    redact_proxy_userinfo(proxy)
}

/// Redact sensitive patterns in free-text that may reach the UI.
pub fn redact_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut s = redact_proxy_userinfo(text);
    s = redact_labeled_value(&s, "authorization");
    s = redact_labeled_value(&s, "set-cookie");
    s = redact_labeled_value(&s, "cookie");
    s = redact_labeled_value(&s, "token");
    s = redact_labeled_value(&s, "api_key");
    s = redact_labeled_value(&s, "api-key");
    s = redact_labeled_value(&s, "apikey");
    s = redact_labeled_value(&s, "password");
    s = redact_labeled_value(&s, "passwd");
    s = redact_labeled_value(&s, "secret");
    s = redact_bearer(&s);
    for key in [
        "session_token",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "authorization",
        "cookie",
        "api_key",
        "apiKey",
        "apikey",
        "password",
        "secret",
    ] {
        s = strip_json_string_field(&s, key);
    }
    s = redact_sk_tokens(&s);
    s
}

const SECRET_STARTERS: &[&str] = &[
    "authorization",
    "set-cookie",
    "cookie",
    "token",
    "api_key",
    "api-key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "bearer",
    "sk-proj-",
    "sk-",
    "http://",
    "https://",
];

fn suffix_secret_hold(raw: &str) -> usize {
    if raw.is_empty() {
        return 0;
    }
    let lower = raw.to_ascii_lowercase();
    let bytes = lower.as_bytes();
    let mut hold = 0usize;
    for starter in SECRET_STARTERS {
        let max = starter.len().min(lower.len());
        for n in (1..=max).rev() {
            if bytes.ends_with(&starter.as_bytes()[..n]) {
                let start = bytes.len() - n;
                let boundary = start == 0 || !bytes[start - 1].is_ascii_alphanumeric();
                if boundary {
                    hold = hold.max(n);
                    break;
                }
            }
        }
    }
    if let Some(pos) = lower.rfind("sk-") {
        let boundary = pos == 0
            || !lower
                .as_bytes()
                .get(pos.wrapping_sub(1))
                .copied()
                .unwrap_or(b' ')
                .is_ascii_alphanumeric();
        if boundary {
            let mut rest = &raw[pos + 3..];
            if rest.len() >= 5 && rest[..5].eq_ignore_ascii_case("proj-") {
                rest = &rest[5..];
            }
            if rest.len() < 8
                && rest
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
            {
                hold = hold.max(raw.len() - pos);
            }
        }
    }
    hold
}

fn floor_char_boundary(s: &str, mut i: usize) -> usize {
    if i >= s.len() {
        return s.len();
    }
    while i > 0 && !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

/// Cross-chunk display redaction: hold incomplete secret prefixes; never flash
/// unchecked fragments. When a pattern matches, the fully redacted string is shown.
pub fn safe_redacted_display(raw: &str, flushed: bool) -> String {
    let redacted = redact_text(raw);
    if flushed {
        return redacted;
    }
    if redacted != raw {
        return redacted;
    }
    let hold = suffix_secret_hold(raw);
    let cut = floor_char_boundary(raw, raw.len().saturating_sub(hold));
    raw[..cut].to_string()
}

/// Accumulating redactor for thinking / assistant stream chunks.
#[derive(Default)]
pub struct StreamRedactor {
    raw: String,
}

impl StreamRedactor {
    pub fn new() -> Self {
        Self { raw: String::new() }
    }

    pub fn push(&mut self, chunk: &str) -> String {
        self.raw.push_str(chunk);
        safe_redacted_display(&self.raw, false)
    }

    pub fn flush(&mut self) -> String {
        safe_redacted_display(&self.raw, true)
    }
}

fn redact_proxy_userinfo(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if let Some(rel) = find_subslice(&bytes[i..], b"://") {
            out.push_str(&s[i..i + rel + 3]);
            i += rel + 3;
            if let Some(at) = bytes[i..].iter().position(|&b| b == b'@') {
                let userinfo = &s[i..i + at];
                if !userinfo.is_empty()
                    && !userinfo.chars().any(|c| c.is_whitespace())
                    && (userinfo.contains(':') || !userinfo.is_empty())
                {
                    out.push_str("***:***");
                    i += at;
                    continue;
                }
            }
        } else {
            out.push_str(&s[i..]);
            break;
        }
    }
    if out.is_empty() {
        s.to_string()
    } else {
        out
    }
}

fn find_subslice(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// `label` then optional whitespace, `:` or `=`, optional `Bearer `, then the value.
fn redact_labeled_value(s: &str, label: &str) -> String {
    let lower = s.to_ascii_lowercase();
    let lab = label.to_ascii_lowercase();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while let Some(pos) = lower[i..].find(&lab) {
        let abs = i + pos;
        // Non-alnum boundary: "stoken=" is not "token="; "session_token=" is.
        if abs > 0 {
            let prev = s[..abs].chars().next_back().unwrap_or('\0');
            if prev.is_ascii_alphanumeric() {
                out.push_str(&s[i..abs + lab.len()]);
                i = abs + lab.len();
                continue;
            }
        }
        out.push_str(&s[i..abs]);
        out.push_str(&s[abs..abs + lab.len()]);
        let after_label = &s[abs + lab.len()..];
        let trimmed = after_label.trim_start();
        let ws1 = after_label.len() - trimmed.len();
        let Some(sep) = trimmed.chars().next() else {
            i = abs + lab.len();
            continue;
        };
        if sep != ':' && sep != '=' {
            i = abs + lab.len();
            continue;
        }
        out.push_str(&after_label[..ws1]);
        out.push(sep);
        let after_sep = trimmed[sep.len_utf8()..].trim_start();
        let ws2 = trimmed[sep.len_utf8()..].len() - after_sep.len();
        out.push_str(&trimmed[sep.len_utf8()..sep.len_utf8() + ws2]);

        let after_l = after_sep.to_ascii_lowercase();
        let (value, bearer_prefix) = if after_l.starts_with("bearer ") {
            let rest = after_sep["bearer ".len()..].trim_start();
            (rest, after_sep.len() - rest.len())
        } else {
            (after_sep, 0)
        };
        out.push_str("***");
        let val_end = value_end(value);
        i = abs + lab.len() + ws1 + sep.len_utf8() + ws2 + bearer_prefix + val_end;
    }
    out.push_str(&s[i..]);
    out
}

fn value_end(value: &str) -> usize {
    value
        .find(|c: char| c.is_whitespace() || matches!(c, '"' | '\'' | ',' | ';' | '}' | ']' | '&'))
        .unwrap_or(value.len())
}

fn redact_bearer(s: &str) -> String {
    let lower = s.to_ascii_lowercase();
    let mut out = String::new();
    let mut last = 0;
    let mut search = 0;
    while let Some(pos) = lower[search..].find("bearer ") {
        let abs = search + pos;
        if abs > 0 {
            let prev = s[..abs].chars().next_back().unwrap_or('\0');
            if prev.is_ascii_alphanumeric() {
                search = abs + 7;
                continue;
            }
        }
        out.push_str(&s[last..abs]);
        out.push_str("Bearer ***");
        let rest = abs + "bearer ".len();
        let val_end = s[rest..]
            .find(|c: char| c.is_whitespace() || matches!(c, '"' | ',' | ';' | '}'))
            .map(|n| rest + n)
            .unwrap_or(s.len());
        last = val_end;
        search = val_end;
    }
    out.push_str(&s[last..]);
    out
}

fn strip_json_string_field(s: &str, field: &str) -> String {
    let needle = format!("\"{field}\"");
    let lower = s.to_ascii_lowercase();
    let needle_l = needle.to_ascii_lowercase();
    let mut out = String::new();
    let mut last = 0;
    let mut search = 0;
    while let Some(pos) = lower[search..].find(&needle_l) {
        let abs = search + pos;
        let after_key = abs + needle.len();
        let rest = s[after_key..].trim_start();
        if let Some(colon) = rest.strip_prefix(':') {
            let val = colon.trim_start();
            if let Some(stripped) = val.strip_prefix('"') {
                if let Some(end) = stripped.find('"') {
                    out.push_str(&s[last..after_key]);
                    let colon_off = s[after_key..].find(':').unwrap_or(0);
                    let colon_abs = after_key + colon_off;
                    let after_colon = &s[colon_abs + 1..];
                    let quote_rel = after_colon.find('"').unwrap_or(0);
                    let open = colon_abs + 1 + quote_rel;
                    out.push_str(&s[after_key..open + 1]);
                    out.push_str("***");
                    last = open + 1 + end + 1;
                    search = last;
                    continue;
                }
            }
        }
        search = abs + needle.len();
    }
    out.push_str(&s[last..]);
    out
}

fn redact_sk_tokens(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < bytes.len() {
        if i + 3 <= bytes.len() && &bytes[i..i + 3] == b"sk-" {
            let boundary = i == 0 || !bytes[i - 1].is_ascii_alphanumeric();
            if boundary {
                let start = i;
                i += 3;
                if i + 5 <= bytes.len() && &bytes[i..i + 5] == b"proj-" {
                    i += 5;
                }
                let val_start = i;
                while i < bytes.len()
                    && (bytes[i].is_ascii_alphanumeric()
                        || matches!(bytes[i], b'.' | b'_' | b'-'))
                {
                    i += 1;
                }
                if i - val_start >= 8 {
                    out.push_str("sk-***");
                    continue;
                }
                out.push_str(&s[start..i]);
                continue;
            }
        }
        let ch = s[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_authorization_bearer_token() {
        let s = redact_text("Authorization: Bearer sk-secretTEST99abc");
        assert!(!s.contains("sk-secretTEST99abc"), "{s}");
        assert!(!s.contains("secretTEST"), "{s}");
        assert!(s.contains("***"), "{s}");
    }

    #[test]
    fn strips_authorization_equals_and_header() {
        let s = redact_text("authorization=Bearer tok_LIVE_abcDEF123456");
        assert!(!s.contains("tok_LIVE_abcDEF123456"), "{s}");
        let s2 = redact_text("AUTHORIZATION: tok_LIVE_abcDEF123456");
        assert!(!s2.contains("tok_LIVE_abcDEF123456"), "{s2}");
    }

    #[test]
    fn strips_cookie_header_and_assignment() {
        let s = redact_text("Cookie: sessionid=TEST_SECRET_VALUE_DO_NOT_LOG");
        assert!(!s.contains("TEST_SECRET_VALUE_DO_NOT_LOG"), "{s}");
        let s2 = redact_text("cookie=SESSIONID_SUPER_SECRET");
        assert!(!s2.contains("SESSIONID_SUPER_SECRET"), "{s2}");
        let s3 = redact_text("Set-Cookie: sid=abc123SECRET; Path=/");
        assert!(!s3.contains("abc123SECRET"), "{s3}");
    }

    #[test]
    fn strips_token_assignment_and_json() {
        let s = redact_text("token=abc123SECRETVALUE");
        assert!(!s.contains("abc123SECRETVALUE"), "{s}");
        let s2 = redact_text(r#"{"token":"super-secret-token"}"#);
        assert!(!s2.contains("super-secret-token"), "{s2}");
        assert!(s2.contains("***"), "{s2}");
    }

    #[test]
    fn leaves_innocent_prose() {
        let src = "retry on stall; notes about cookies in the jar";
        assert_eq!(redact_text(src), src);
    }

    #[test]
    fn proxy_userinfo_in_prose() {
        let s = redact_text("use http://user:s3cretPASS@127.0.0.1:7890");
        assert!(!s.contains("s3cretPASS"), "{s}");
        assert!(s.contains("***:***"), "{s}");
    }

    #[test]
    fn stream_redactor_cross_chunk_secret() {
        let mut r = StreamRedactor::new();
        let a = r.push("token=abc");
        assert!(!a.contains("abc123SECRETVALUE"), "{a}");
        let b = r.push("123SECRETVALUE more");
        assert!(!b.contains("SECRETVALUE"), "{b}");
        assert!(!b.contains("abc123"), "{b}");
        let c = r.flush();
        assert!(!c.contains("SECRETVALUE"), "{c}");
        assert!(c.contains("***"), "{c}");
    }

    #[test]
    fn stream_redactor_holds_incomplete_sk_prefix() {
        let mut r = StreamRedactor::new();
        let a = r.push("sk-");
        assert!(a.is_empty() || a == "sk-", "held or prefix only: {a}");
        assert!(!a.contains("secretTEST99abc"));
        let b = r.push("secretTEST99abc");
        assert!(!b.contains("secretTEST99abc"), "{b}");
        assert!(b.contains("sk-***") || b.contains("***"), "{b}");
    }

    #[test]
    fn safe_display_innocent_passes() {
        assert_eq!(safe_redacted_display("click a → done ok", true), "click a → done ok");
    }
}
