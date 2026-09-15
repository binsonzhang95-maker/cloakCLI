//! CLI: teach start validates names, refuses headless-equivalent misuse,
//! lock-conflicts clearly, and headed smoke is skipped without a display.

use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

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


fn read_until(reader: &mut BufReader<impl Read>, needle: &str, secs: u64) -> String {
    let deadline = std::time::Instant::now() + Duration::from_secs(secs);
    let mut out = String::new();
    while std::time::Instant::now() < deadline {
        let mut l = String::new();
        match reader.read_line(&mut l) {
            Ok(0) => break,
            Ok(_) => {
                out.push_str(&l);
                if l.contains(needle) {
                    break;
                }
            }
            Err(_) => break,
        }
    }
    out
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
    let chat = bin()
        .args(["teach", "chat", "--help"])
        .output()
        .expect("run");
    assert!(chat.status.success(), "{}", combined(&chat));
    let c = combined(&chat);
    assert!(c.contains("mock-json") || c.contains("mock_json") || c.contains("Chat"), "{c}");
    assert!(c.contains("events"), "{c}");
    assert!(c.contains("no-browser"), "{c}");
    let turn = bin()
        .args(["teach", "turn", "--help"])
        .output()
        .expect("run");
    assert!(turn.status.success(), "{}", combined(&turn));
    let t = combined(&turn);
    assert!(t.contains("goal") && t.contains("mock-json"), "{t}");
    let export = bin()
        .args(["teach", "export", "--help"])
        .output()
        .expect("run");
    assert!(export.status.success(), "{}", combined(&export));
    let e = combined(&export);
    assert!(e.contains("steps-json") || e.contains("steps_json"), "{e}");
    assert!(e.contains("overwrite"), "{e}");
}

#[test]
fn turn_validates_mock_json_and_rejects_danger() {
    let home = tmp_home();
    let ok = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "turn",
            "--goal",
            "click the link",
            "--mock-json",
            r#"{"schema_version":1,"actions":[{"action":"click","selector":"a"},{"action":"done","reason":"ok"}]}"#,
        ])
        .output()
        .expect("run");
    assert!(ok.status.success(), "{}", combined(&ok));
    let t = combined(&ok);
    assert!(t.contains("VALIDATE_OK"), "{t}");
    assert!(t.contains("click"), "{t}");

    let bad = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "turn",
            "--goal",
            "pwn",
            "--mock-json",
            r#"{"schema_version":1,"actions":[{"action":"shell","cmd":"id"}]}"#,
        ])
        .output()
        .expect("run");
    assert!(!bad.status.success(), "{}", combined(&bad));
    let t = combined(&bad);
    assert!(t.contains("VALIDATE_FAIL") || t.contains("forbidden"), "{t}");

    let over = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "turn",
            "--goal",
            "many",
            "--mock-json",
            r#"{"schema_version":1,"actions":[{"action":"wait","ms":1},{"action":"wait","ms":1},{"action":"wait","ms":1},{"action":"done","reason":"x"}]}"#,
        ])
        .output()
        .expect("run");
    assert!(!over.status.success(), "{}", combined(&over));

    let cross = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "turn",
            "--goal",
            "go elsewhere",
            "--allow-origin",
            "https://example.com",
            "--current-origin",
            "https://example.com",
            "--mock-json",
            r#"{"schema_version":1,"actions":[{"action":"goto","url":"https://other.example/login"}]}"#,
        ])
        .output()
        .expect("run");
    assert!(cross.status.success(), "{}", combined(&cross));
    let t = combined(&cross);
    assert!(t.contains("VALIDATE_OK"), "{t}");
    assert!(!t.contains("NEEDS_CONFIRM"), "{t}");

    let any_https = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "turn",
            "--goal",
            "paste url",
            "--allow-origin",
            "https://example.com",
            "--mock-json",
            r#"{"schema_version":1,"actions":[{"action":"goto","url":"https://paste.example/doc"}]}"#,
        ])
        .output()
        .expect("run");
    assert!(any_https.status.success(), "{}", combined(&any_https));
    let t = combined(&any_https);
    assert!(t.contains("VALIDATE_OK"), "{t}");
    assert!(t.contains("goto"), "{t}");

    let js = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "turn",
            "--goal",
            "pwn",
            "--mock-json",
            r#"{"schema_version":1,"actions":[{"action":"goto","url":"javascript:alert(1)"}]}"#,
        ])
        .output()
        .expect("run");
    assert!(!js.status.success(), "{}", combined(&js));
    let t = combined(&js);
    assert!(t.contains("VALIDATE_FAIL") || t.contains("javascript"), "{t}");
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn export_writes_draft_and_refuses_overwrite() {
    let home = tmp_home();
    let steps = r##"[{"action":"goto","url":"https://example.com/login?token=leakme&next=/app","source":"llm"},{"action":"fill","selector":"#user","text":"alice","field_name":"username","source":"human"},{"action":"fill","selector":"#pass","text":"hunter2","field_name":"password","source":"human"},{"action":"click","selector":"button.submit","source":"human"}]"##;
    let ok = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "export",
            "--name",
            "taught-login",
            "--goal",
            "Sign in and open the dashboard",
            "--steps-json",
            steps,
        ])
        .output()
        .expect("run");
    assert!(ok.status.success(), "{}", combined(&ok));
    let t = combined(&ok);
    assert!(t.contains("EXPORT_OK"), "{t}");
    let sj = home.join("skills/taught-login/skill.json");
    assert!(sj.is_file(), "{}", sj.display());
    let body = fs::read_to_string(&sj).unwrap();
    assert!(body.contains("\"source\": \"agent\"") || body.contains("\"source\":\"agent\""));
    assert!(body.contains("human"));
    assert!(body.contains("{{vars.PASSWORD}}"));
    assert!(!body.contains("hunter2"));
    assert!(!body.contains("leakme"));
    assert!(!body.contains("pairing"));
    let original = body.clone();

    let dup = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "export",
            "--name",
            "taught-login",
            "--goal",
            "Sign in and open the dashboard",
            "--steps-json",
            steps,
        ])
        .output()
        .expect("run");
    assert!(!dup.status.success(), "{}", combined(&dup));
    let t = combined(&dup);
    assert!(t.contains("already exists"), "{t}");
    assert_eq!(fs::read_to_string(&sj).unwrap(), original);

    let danger = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "export",
            "--name",
            "evil",
            "--steps-json",
            r#"[{"action":"shell","cmd":"id"}]"#,
        ])
        .output()
        .expect("run");
    assert!(!danger.status.success(), "{}", combined(&danger));
    assert!(!home.join("skills/evil/skill.json").exists());

    let escape = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "export",
            "--name",
            "../etc",
            "--steps-json",
            r#"[{"action":"goto","url":"https://example.com/"}]"#,
        ])
        .output()
        .expect("run");
    assert!(!escape.status.success(), "{}", combined(&escape));
    let _ = fs::remove_dir_all(&home);
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
    assert!(t.contains("no-smart-optimize"), "{t}");
}

#[test]
fn start_preflight_missing_browser_is_clear() {
    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));

    let fake = home.join("fake_python");
    fs::write(
        &fake,
        r#"#!/usr/bin/env python3
import json, sys
if len(sys.argv) >= 3 and sys.argv[1] == "-c" and "binary_info" in sys.argv[2]:
    print(json.dumps({"path": "/nonexistent/cloak-chrome", "installed": False}))
    sys.exit(3)
sys.stderr.write("unexpected %r\n" % (sys.argv,))
sys.exit(1)
"#,
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&fake, fs::Permissions::from_mode(0o755)).unwrap();
    }

    let out = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_PYTHON", &fake)
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
    assert!(!out.status.success());
    let t = combined(&out);
    assert!(
        t.contains("binary not found") || t.to_ascii_lowercase().contains("chromium binary"),
        "{t}"
    );
    assert!(!t.contains("TeachingSession"), "{t}");
    let _ = fs::remove_dir_all(&home);
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

#[test]
fn events_no_browser_mock_turn_and_redacts() {
    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));

    let mock = r#"{"schema_version":1,"actions":[{"action":"click","selector":"a"},{"action":"done","reason":"ok"}]}"#;
    let mut child = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_MS", "0")
        .args([
            "teach",
            "chat",
            "--profile",
            "demo",
            "--events",
            "--no-browser",
            "--mock-json",
            mock,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn events");

    let mut stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");
    let mut reader = BufReader::new(stdout);
    let mut session_line = String::new();
    reader
        .read_line(&mut session_line)
        .expect("session event");
    assert!(
        session_line.contains("\"kind\":\"session\"") || session_line.contains("\"kind\": \"session\""),
        "first event should be session: {session_line}"
    );
    assert!(session_line.contains("demo"), "{session_line}");

    writeln!(
        stdin,
        r#"{{"cmd":"send","goal":"click the link token=abc123SECRETVALUE","profile":"demo"}}"#
    )
    .unwrap();
    // Wait for the turn to finish before stop — under parallel cargo test load,
    // an immediate stop can cancel during planning and flake the assistant asserts.
    let mut rest = read_until(&mut reader, "\"kind\":\"assistant\"", 8);
    if !rest.contains("\"kind\":\"assistant\"") && !rest.contains("assistant_delta") {
        panic!("turn did not stream before stop: {rest}");
    }
    writeln!(stdin, r#"{{"cmd":"stop"}}"#).unwrap();
    drop(stdin);
    let mut tail = String::new();
    let _ = reader.read_to_string(&mut tail);
    rest.push_str(&tail);
    let status = child
        .wait_timeout()
        .unwrap_or_else(|_| child.wait().expect("wait"));
    let stderr = {
        let mut s = String::new();
        if let Some(mut err) = child.stderr.take() {
            let _ = std::io::Read::read_to_string(&mut err, &mut s);
        }
        s
    };
    let all = format!("{session_line}{rest}");
    assert!(status.success(), "events exit {:?}\n{all}\n{stderr}", status.code());
    assert!(all.contains("\"kind\":\"user\"") || all.contains("\"kind\": \"user\""), "{all}");
    assert!(all.contains("\"kind\":\"assistant\"") || all.contains("\"kind\": \"assistant\""), "{all}");
    assert!(all.contains("\"kind\":\"job\"") || all.contains("\"kind\": \"job\""), "{all}");
    assert!(all.contains("\"kind\":\"closed\"") || all.contains("\"kind\": \"closed\""), "{all}");
    assert!(!all.contains("abc123SECRETVALUE"), "secret leaked: {all}");
    assert!(!stderr.contains("abc123SECRETVALUE"), "secret on stderr: {stderr}");
    assert!(
        all.contains("\"kind\":\"assistant_delta\"") || all.contains("\"kind\": \"assistant_delta\""),
        "expected incremental assistant_delta: {all}"
    );
    assert!(
        !all.contains("thinking_delta"),
        "no provider reasoning → must not fabricate thinking_delta: {all}"
    );
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn events_reject_unknown_cmd() {
    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));

    let mut child = bin()
        .env("CLOAKCLI_HOME", &home)
        .args([
            "teach",
            "chat",
            "--profile",
            "demo",
            "--events",
            "--no-browser",
            "--mock-json",
            r#"{"schema_version":1,"actions":[{"action":"done","reason":"ok"}]}"#,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    let mut stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    reader.read_line(&mut line).expect("session");
    writeln!(stdin, r#"{{"cmd":"explode","shell":"rm -rf /"}}"#).unwrap();
    writeln!(stdin, r#"{{"cmd":"stop"}}"#).unwrap();
    drop(stdin);
    let mut rest = String::new();
    let _ = reader.read_to_string(&mut rest);
    let _ = child.wait_timeout();
    let all = format!("{line}{rest}");
    assert!(
        all.contains("\"code\":\"bad_cmd\"") || all.contains("invalid command"),
        "expected bad_cmd for unknown cmd: {all}"
    );
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn events_stream_deltas_and_cancel_mid_stream() {
    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));

    let mock = r##"{"schema_version":1,"actions":[{"action":"click","selector":"#go"},{"action":"done","reason":"ok"}]}"##;
    let mut child = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_MS", "40")
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_CHARS", "4")
        .args([
            "teach",
            "chat",
            "--profile",
            "demo",
            "--events",
            "--no-browser",
            "--mock-json",
            mock,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    let mut stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");
    let mut reader = BufReader::new(stdout);

    let deadline = std::time::Instant::now() + Duration::from_secs(8);
    let mut saw_delta = false;
    loop {
        if std::time::Instant::now() > deadline {
            break;
        }
        let mut line = String::new();
        reader.read_line(&mut line).ok();
        if line.contains("\"kind\":\"session\"") || line.contains("\"kind\": \"session\"") {
            writeln!(
                stdin,
                r#"{{"cmd":"send","goal":"click go","profile":"demo"}}"#
            )
            .unwrap();
            let _ = stdin.flush();
        }
        if line.contains("assistant_delta") {
            saw_delta = true;
            writeln!(stdin, r#"{{"cmd":"cancel"}}"#).unwrap();
            let _ = stdin.flush();
            break;
        }
    }
    assert!(saw_delta, "never saw assistant_delta before cancel");
    writeln!(stdin, r#"{{"cmd":"stop"}}"#).unwrap();
    drop(stdin);
    let mut rest = String::new();
    let _ = reader.read_to_string(&mut rest);
    let _ = child.wait_timeout();
    assert!(
        rest.contains("cancelled") || rest.contains("\"state\":\"cancelled\""),
        "expected cancelled job after mid-stream cancel: {rest}"
    );
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn events_resume_after_child_exit() {
    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));
    let mock = r#"{"schema_version":1,"actions":[{"action":"click","selector":"a"},{"action":"done","reason":"ok"}]}"#;

    let mut child = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_MS", "0")
        .args([
            "teach",
            "chat",
            "--profile",
            "demo",
            "--events",
            "--no-browser",
            "--mock-json",
            mock,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn 1");
    let mut stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    reader.read_line(&mut line).expect("session");
    writeln!(
        stdin,
        r#"{{"cmd":"send","goal":"click the link","profile":"demo"}}"#
    )
    .unwrap();
    let deadline = std::time::Instant::now() + Duration::from_secs(8);
    let mut saw_assistant = false;
    let mut rest = String::new();
    while std::time::Instant::now() < deadline {
        let mut l = String::new();
        if reader.read_line(&mut l).unwrap_or(0) == 0 {
            break;
        }
        rest.push_str(&l);
        if l.contains("\"kind\":\"assistant\"") && l.contains("\"done\":true")
            || (l.contains("\"kind\":\"assistant\"") && !l.contains("assistant_delta"))
        {
            saw_assistant = true;
            break;
        }
    }
    assert!(saw_assistant, "first session never finished assistant: {rest}");
    writeln!(stdin, r#"{{"cmd":"stop"}}"#).unwrap();
    drop(stdin);
    let _ = child.wait_timeout();

    let snap = home.join("data").join("teach").join("events-snapshot.json");
    assert!(snap.is_file(), "snapshot missing at {}", snap.display());

    let mut child2 = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_MS", "0")
        .args([
            "teach",
            "chat",
            "--profile",
            "demo",
            "--events",
            "--no-browser",
            "--mock-json",
            mock,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn 2");
    let mut stdin2 = child2.stdin.take().expect("stdin");
    let stdout2 = child2.stdout.take().expect("stdout");
    let mut reader2 = BufReader::new(stdout2);
    let mut blob = String::new();
    let deadline = std::time::Instant::now() + Duration::from_secs(6);
    let mut saw_resume = false;
    while std::time::Instant::now() < deadline {
        let mut l = String::new();
        if reader2.read_line(&mut l).unwrap_or(0) == 0 {
            break;
        }
        blob.push_str(&l);
        if l.contains("\"kind\":\"resume\"") || l.contains("\"kind\": \"resume\"") {
            saw_resume = true;
            break;
        }
    }
    assert!(saw_resume, "reconnect did not emit resume: {blob}");
    assert!(
        blob.contains("click the link") || blob.contains("new_hub"),
        "resume missing transcript/hub note: {blob}"
    );
    writeln!(
        stdin2,
        r#"{{"cmd":"send","goal":"again","profile":"demo"}}"#
    )
    .unwrap();
    writeln!(stdin2, r#"{{"cmd":"stop"}}"#).unwrap();
    drop(stdin2);
    let mut rest2 = String::new();
    let _ = reader2.read_to_string(&mut rest2);
    let _ = child2.wait_timeout();
    let all2 = format!("{blob}{rest2}");
    assert!(
        all2.contains("\"kind\":\"assistant\"") || all2.contains("assistant_delta"),
        "continue after resume produced no assistant: {all2}"
    );
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn events_thinking_mock_summary_then_assistant_delta() {
    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));

    let mock = r#"{"schema_version":1,"actions":[{"action":"done","reason":"ok"}]}"#;
    let mut child = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_MS", "0")
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_CHARS", "8")
        .env("CLOAKCLI_TEACH_THINKING_MOCK", "check selector then click")
        .args([
            "teach",
            "chat",
            "--profile",
            "demo",
            "--events",
            "--no-browser",
            "--mock-json",
            mock,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    let mut stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");
    let mut reader = BufReader::new(stdout);
    let mut session_line = String::new();
    reader.read_line(&mut session_line).expect("session");
    writeln!(
        stdin,
        r#"{{"cmd":"send","goal":"click go","profile":"demo"}}"#
    )
    .unwrap();
    let mut rest = read_until(&mut reader, "thinking_delta", 8);
    assert!(
        rest.contains("thinking_delta"),
        "expected thinking_delta before stop: {rest}"
    );
    let more = read_until(&mut reader, "\"kind\":\"assistant_delta\"", 8);
    rest.push_str(&more);
    if !rest.contains("\"kind\":\"assistant_delta\"") {
        let more = read_until(&mut reader, "\"kind\":\"assistant\"", 4);
        rest.push_str(&more);
    }
    writeln!(stdin, r#"{{"cmd":"stop"}}"#).unwrap();
    drop(stdin);
    let mut tail = String::new();
    let _ = reader.read_to_string(&mut tail);
    rest.push_str(&tail);
    let _ = child.wait_timeout();
    let all = format!("{session_line}{rest}");
    assert!(
        all.contains("\"kind\":\"thinking_delta\""),
        "expected thinking_delta: {all}"
    );
    assert!(
        all.contains("thinking_done") || all.contains("\"kind\":\"thinking_done\""),
        "expected thinking_done: {all}"
    );
    assert!(
        all.contains("\"kind\":\"assistant_delta\""),
        "assistant_delta must still stream: {all}"
    );
    let think_pos = all.find("thinking_delta").expect("thinking_delta pos");
    let asst_pos = all.find("assistant_delta").expect("assistant_delta pos");
    assert!(
        think_pos < asst_pos,
        "thinking should precede assistant: {all}"
    );
    let _ = fs::remove_dir_all(&home);
}

#[test]
fn events_thinking_cross_chunk_secret_redacted() {
    let home = tmp_home();
    let created = bin()
        .env("CLOAKCLI_HOME", &home)
        .args(["profile", "create", "demo"])
        .output()
        .expect("create");
    assert!(created.status.success(), "{}", combined(&created));

    let mock = r#"{"schema_version":1,"actions":[{"action":"done","reason":"ok"}]}"#;
    let mut child = bin()
        .env("CLOAKCLI_HOME", &home)
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_MS", "0")
        .env("CLOAKCLI_TEACH_STREAM_CHUNK_CHARS", "4")
        .env(
            "CLOAKCLI_TEACH_THINKING_MOCK",
            "use token=abc123SECRETVALUE then click",
        )
        .args([
            "teach",
            "chat",
            "--profile",
            "demo",
            "--events",
            "--no-browser",
            "--mock-json",
            mock,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    let mut stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");
    let mut reader = BufReader::new(stdout);
    let mut session_line = String::new();
    reader.read_line(&mut session_line).expect("session");
    writeln!(
        stdin,
        r#"{{"cmd":"send","goal":"go","profile":"demo"}}"#
    )
    .unwrap();
    let mut rest = read_until(&mut reader, "thinking_delta", 8);
    assert!(
        rest.contains("thinking_delta"),
        "expected thinking_delta before stop: {rest}"
    );
    // Keep the turn alive until assistant streams — stop would cancel planning.
    let more = read_until(&mut reader, "assistant_delta", 8);
    rest.push_str(&more);
    writeln!(stdin, r#"{{"cmd":"stop"}}"#).unwrap();
    drop(stdin);
    let mut tail = String::new();
    let _ = reader.read_to_string(&mut tail);
    rest.push_str(&tail);
    let stderr = {
        let mut s = String::new();
        if let Some(mut err) = child.stderr.take() {
            let _ = std::io::Read::read_to_string(&mut err, &mut s);
        }
        s
    };
    let _ = child.wait_timeout();
    let all = format!("{session_line}{rest}");
    assert!(all.contains("thinking_delta"), "expected thinking_delta: {all}");
    assert!(
        !all.contains("abc123SECRETVALUE"),
        "thinking secret leaked on stdout: {all}"
    );
    assert!(
        !stderr.contains("abc123SECRETVALUE"),
        "thinking secret leaked on stderr: {stderr}"
    );
    assert!(
        all.contains("assistant_delta"),
        "assistant_delta regression: {all}"
    );
    let _ = fs::remove_dir_all(&home);
}

trait WaitTimeout {
    fn wait_timeout(&mut self) -> std::io::Result<std::process::ExitStatus>;
}

impl WaitTimeout for std::process::Child {
    fn wait_timeout(&mut self) -> std::io::Result<std::process::ExitStatus> {
        let deadline = std::time::Instant::now() + Duration::from_secs(20);
        loop {
            match self.try_wait()? {
                Some(st) => return Ok(st),
                None => {
                    if std::time::Instant::now() > deadline {
                        let _ = self.kill();
                        return self.wait();
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
            }
        }
    }
}
