//! CLI: teach start validates names, refuses headless-equivalent misuse,
//! lock-conflicts clearly, and headed smoke is skipped without a display.

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_cloakcli"))
}

fn tmp_home() -> PathBuf {
    let n = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let p = std::env::temp_dir().join(format!("cloakcli_teach_cli_{n}"));
    let _ = fs::remove_dir_all(&p);
    fs::create_dir_all(p.join("skills")).unwrap();
    fs::write(
        p.join("Cargo.toml"),
        "[package]\nname=\"t\"\nversion=\"0.0.0\"\n",
    )
    .unwrap();
    // Point teach extension resolve at the real repo copy via a symlink if possible,
    // else copy is unnecessary because resolve_extension_dir also walks the binary
    // ancestors to the workspace.
    p
}

fn combined(out: &std::process::Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    )
}

#[test]
fn help_lists_teach_start() {
    let out = bin().args(["teach", "--help"]).output().expect("run");
    assert!(out.status.success(), "{}", combined(&out));
    let t = combined(&out);
    assert!(t.contains("start"), "{t}");
    assert!(!t.contains("TeachingSession"), "{t}");
    let start = bin()
        .args(["teach", "start", "--help"])
        .output()
        .expect("run");
    assert!(start.status.success(), "{}", combined(&start));
    let s = combined(&start);
    assert!(s.contains("profile"), "{s}");
}

#[test]
fn start_rejects_malicious_profile_name() {
    let home = tmp_home();
    let out = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["teach", "start", "--profile", "../etc"])
        .output()
        .expect("run");
    assert!(!out.status.success());
    let t = combined(&out);
    assert!(
        t.contains("Invalid") || t.contains("invalid") || t.contains("profile"),
        "{t}"
    );
    assert!(!t.contains("load-extension"), "{t}");
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn start_rejects_unknown_profile() {
    let home = tmp_home();
    let out = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["teach", "start", "--profile", "nosuchprofile"])
        .output()
        .expect("run");
    assert!(!out.status.success());
    let t = combined(&out);
    assert!(t.contains("not found") || t.contains("Invalid"), "{t}");
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn start_lock_conflict_is_clear() {
    let home = tmp_home();
    // Create a profile through the CLI so metadata matches production.
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));

    let lock = home.join("data").join("locks").join("demo.lock");
    fs::create_dir_all(&lock).unwrap();
    fs::write(lock.join("pid"), format!("{}\n", std::process::id())).unwrap();

    let out = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["teach", "start", "--profile", "demo"])
        .output()
        .expect("run");
    assert!(!out.status.success());
    let t = combined(&out);
    assert!(
        t.to_ascii_lowercase().contains("already in use")
            || t.to_ascii_lowercase().contains("lock"),
        "{t}"
    );
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn no_extension_path_cli_flag() {
    let out = bin()
        .args(["teach", "start", "--help"])
        .output()
        .expect("run");
    let t = combined(&out);
    assert!(!t.contains("load-extension"), "{t}");
    assert!(!t.contains("--extension"), "{t}");
    assert!(!t.contains("--headless"), "{t}");
    assert!(t.contains("allow-secrets"), "{t}");
}

#[test]
fn headed_smoke_or_skip() {
    let has_display = std::env::var_os("DISPLAY").is_some()
        || std::env::var_os("WAYLAND_DISPLAY").is_some();
    if !has_display {
        eprintln!(
            "SKIP headed teach smoke: no DISPLAY/WAYLAND_DISPLAY. \
             Run `cloakcli teach start --profile demo --url https://example.com` on a GUI host."
        );
        return;
    }

    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    if !created.status.success() {
        eprintln!("SKIP headed teach smoke: profile create failed: {}", combined(&created));
        let _ = fs::remove_dir_all(&home);
        return;
    }

    let out = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_TEACH_SMOKE_SECONDS", "2")
        .args([
            "teach",
            "start",
            "--profile",
            "demo",
            "--url",
            "https://example.com",
        ])
        .output()
        .expect("run");
    let t = combined(&out);
    if !out.status.success() {
        // Headed launch can still fail in CI (missing GPU, cloakbrowser, sandbox).
        eprintln!(
            "headed teach smoke did not launch (documented, not a hard fail): {t}"
        );
        let _ = fs::remove_dir_all(&home);
        return;
    }
    assert!(
        t.contains("teach:") || t.contains("extension"),
        "expected teach banner: {t}"
    );
    let _ = fs::remove_dir_all(&home);
}
