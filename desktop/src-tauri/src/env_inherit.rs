//! Whitelist process environment inherited by `cloakcli tui`.

use std::collections::BTreeMap;
use std::env;
use std::ffi::OsString;

/// Keys copied from the desktop process into the PTY child.
/// Tokens are inherited when present (the TUI needs them) but never logged.
const EXACT: &[&str] = &[
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TERM",
    "COLORTERM",
    "TZ",
    "TMPDIR",
    "TMP",
    "TEMP",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "SSH_AUTH_SOCK",
    "NO_COLOR",
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "no_proxy",
    "NO_PROXY",
    "CLOAKCLI_PYTHON",
    "CLOAKCLI_HEADED",
    "CLOAKCLI_IPC_TIMEOUT",
    "CLOAKCLI_MASTER_BIND",
    "CLOAKCLI_MASTER_TOKEN",
    "CLOAKCLI_ANIMATIONS",
];

pub fn inherited_env() -> BTreeMap<OsString, OsString> {
    let mut out = BTreeMap::new();
    for (key, value) in env::vars_os() {
        let Some(name) = key.to_str() else { continue };
        if is_whitelisted(name) {
            out.insert(key, value);
        }
    }
    out
}

pub fn is_whitelisted(name: &str) -> bool {
    if name == "CLOAKCLI_BIN" {
        return false;
    }
    if name == "OPENAI_API_KEY" {
        return true;
    }
    if name.starts_with("CLOAKCLI_") {
        return true;
    }
    EXACT.iter().any(|k| *k == name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allows_path_and_cloakcli_runtime() {
        assert!(is_whitelisted("PATH"));
        assert!(is_whitelisted("CLOAKCLI_PYTHON"));
        assert!(is_whitelisted("CLOAKCLI_MASTER_TOKEN"));
        assert!(is_whitelisted("CLOAKCLI_LLM_API_KEY"));
        assert!(is_whitelisted("CLOAKCLI_TEACH_CHAT_MOCK"));
        assert!(is_whitelisted("OPENAI_API_KEY"));
    }

    #[test]
    fn denies_arbitrary_and_bin_override() {
        assert!(!is_whitelisted("LD_PRELOAD"));
        assert!(!is_whitelisted("CLOAKCLI_BIN"));
        assert!(!is_whitelisted("AWS_SECRET_ACCESS_KEY"));
        assert!(!is_whitelisted("SHELL_COMMAND"));
    }
}
