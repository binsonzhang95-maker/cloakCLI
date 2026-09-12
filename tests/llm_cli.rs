//! CLI integration: `--api-key` must be rejected without echoing the value.

use std::process::Command;

fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_cloakcli"))
}

#[test]
fn configure_rejects_api_key_flag_without_echo() {
    let out = bin()
        .args([
            "llm",
            "configure",
            "--api-key",
            "sk-this-must-not-echo-xyz",
            "--base-url",
            "https://api.openai.com/v1",
            "--model",
            "gpt-4o",
        ])
        .output()
        .expect("run cloakcli");
    assert!(!out.status.success());
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        !combined.contains("sk-this-must-not-echo-xyz"),
        "leaked key: {combined}"
    );
    assert!(
        combined.contains("shell history") || combined.contains("--api-key is rejected"),
        "{combined}"
    );
}

#[test]
fn models_rejects_file_url() {
    let out = bin()
        .args(["llm", "models", "--base-url", "file:///etc/passwd"])
        .output()
        .expect("run cloakcli");
    assert!(!out.status.success());
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        combined.to_ascii_lowercase().contains("http")
            || combined.contains("file:")
            || combined.contains("rejects"),
        "{combined}"
    );
    assert!(!combined.contains("sk-"));
}

#[test]
fn set_rejects_pasted_key_as_env_without_echo() {
    let secret = "sk-pasted-env-secret-xyz";
    let out = bin()
        .args([
            "llm",
            "set",
            "--api-key-env",
            secret,
            "--base-url",
            "https://api.openai.com/v1",
            "--model",
            "gpt-4o",
        ])
        .output()
        .expect("run cloakcli");
    assert!(!out.status.success());
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        !combined.contains(secret),
        "echoed pasted key: {combined}"
    );
    assert!(
        combined.contains("api_key_env") || combined.contains("env"),
        "{combined}"
    );
}

#[test]
fn models_rejects_query_and_fragment_base_url() {
    for url in [
        "https://api.openai.com/v1?x=1",
        "https://api.openai.com/v1#frag",
        "https://user:pass@api.openai.com/v1",
    ] {
        let out = bin()
            .args(["llm", "models", "--base-url", url])
            .output()
            .expect("run cloakcli");
        assert!(!out.status.success(), "accepted {url}");
        let combined = format!(
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        assert!(!combined.contains("user:pass"), "{combined}");
        assert!(!combined.contains("pass@"), "{combined}");
        assert!(
            combined.contains("query")
                || combined.contains("fragment")
                || combined.contains("credentials")
                || combined.contains("http"),
            "{url}: {combined}"
        );
    }
}

#[test]
fn show_does_not_print_key_values() {
    let out = bin().args(["llm", "show"]).output().expect("run cloakcli");
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(!combined.to_ascii_lowercase().contains("api_key\":"));
    assert!(!combined.contains("sk-"));
}
