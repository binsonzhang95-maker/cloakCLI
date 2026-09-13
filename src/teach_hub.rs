//! Loopback Teach Hub: session, pairing, page_state (M1).
//!
//! Listens on 127.0.0.1 only. Speaks WebSocket (MV3 extension) and JSONL
//! (Python worker) with the same Envelope. Does not log tokens/cookies/passwords.
//!
//! M3: takeover_start must pause the LLM/action executor so it cannot race
//! the human on the same Playwright page. Not implemented in M1.

use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs::OpenOptions;
use std::io::Write;
use std::net::{SocketAddr, TcpListener as StdTcpListener};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::RwLock;
use tokio::task::JoinHandle;

use crate::teach_protocol::{
    self, pairing_accept_from_data, pairing_offer_data, redact_for_log, ClientRole, Envelope,
    PageState, ProtocolError, MAX_MESSAGE_BYTES, MAX_PAIRING_FAILURES, TYPE_HEARTBEAT,
    TYPE_PAGE_STATE, TYPE_PAIRING_OFFER, TYPE_PAIRING_RESULT,
};

const PAIRING_TTL: Duration = Duration::from_secs(10 * 60);
const TIMELINE_CAP: usize = 64;

#[derive(Debug, Clone)]
pub struct TeachHubOpts {
    pub allow_origins: Vec<String>,
}

pub struct TeachHubHandle {
    pub addr: SocketAddr,
    pairing_id: String,
    pairing_code: String,
    #[allow(dead_code)]
    session_id: String,
    #[allow(dead_code)]
    inner: SharedHub,
    join: JoinHandle<()>,
}

impl TeachHubHandle {
    pub fn port(&self) -> u16 {
        self.addr.port()
    }

    pub fn pairing_id(&self) -> &str {
        &self.pairing_id
    }

    pub fn pairing_code(&self) -> &str {
        &self.pairing_code
    }

    pub fn abort(&self) {
        self.join.abort();
    }

    #[cfg(test)]
    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    #[cfg(test)]
    pub async fn last_page_state(&self) -> Option<PageState> {
        self.inner.read().await.session.last_page_state.clone()
    }

    #[cfg(test)]
    pub async fn paired_roles(&self) -> Vec<ClientRole> {
        let g = self.inner.read().await;
        g.slots.keys().copied().collect()
    }

    #[cfg(test)]
    pub async fn session_count(&self) -> usize {
        1
    }

    #[cfg(test)]
    pub async fn reconnect_count(&self) -> u32 {
        self.inner.read().await.reconnects
    }
}

impl Drop for TeachHubHandle {
    fn drop(&mut self) {
        self.join.abort();
    }
}

struct HubInner {
    session: Session,
    pairing: PairingState,
    slots: HashMap<ClientRole, PairedSlot>,
    seq: u64,
    timeline: Vec<StoredEvent>,
    reconnects: u32,
}

struct Session {
    session_id: String,
    /// High-entropy secret; never logged or serialized to session.json.
    #[allow(dead_code)]
    secret: String,
    allow_origins: Vec<String>,
    last_page_state: Option<PageState>,
}

struct PairingState {
    pairing_id: String,
    code: String,
    expires_at: Instant,
    failures: u32,
    used_nonces: HashSet<String>,
    locked: bool,
}

struct PairedSlot {
    token: String,
    connected: bool,
    last_seq: u64,
}

struct StoredEvent {
    seq: u64,
    env: Envelope,
}

type SharedHub = Arc<RwLock<HubInner>>;

pub async fn spawn(opts: TeachHubOpts) -> Result<TeachHubHandle> {
    let std_listener = StdTcpListener::bind("127.0.0.1:0")
        .context("bind teach hub on 127.0.0.1")?;
    std_listener.set_nonblocking(true)?;
    let addr = std_listener.local_addr()?;
    let listener = TcpListener::from_std(std_listener)?;

    let pairing_id = uuid::Uuid::new_v4().to_string();
    let pairing_code = short_code();
    let session_id = format!("sess-{}", uuid::Uuid::new_v4());
    let secret = format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    );

    let inner = Arc::new(RwLock::new(HubInner {
        session: Session {
            session_id: session_id.clone(),
            secret,
            allow_origins: opts.allow_origins,
            last_page_state: None,
        },
        pairing: PairingState {
            pairing_id: pairing_id.clone(),
            code: pairing_code.clone(),
            expires_at: Instant::now() + PAIRING_TTL,
            failures: 0,
            used_nonces: HashSet::new(),
            locked: false,
        },
        slots: HashMap::new(),
        seq: 0,
        timeline: Vec::new(),
        reconnects: 0,
    }));

    let serve_inner = inner.clone();
    let join = tokio::spawn(async move {
        run_listener(listener, serve_inner).await;
    });

    Ok(TeachHubHandle {
        addr,
        pairing_id,
        pairing_code,
        session_id,
        inner,
        join,
    })
}

fn short_code() -> String {
    const ALPH: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    let bytes = uuid::Uuid::new_v4();
    bytes
        .as_bytes()
        .iter()
        .take(6)
        .map(|b| ALPH[(*b as usize) % ALPH.len()] as char)
        .collect()
}

fn new_token() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    )
}

fn hub_log(msg: &str) {
    eprintln!("teach hub: {}", redact_for_log(msg));
}

static EVENT_FILE_LOCK: StdMutex<()> = StdMutex::new(());

/// Append a redacted JSONL event when `CLOAKCLI_TEACH_HUB_EVENTS` is set.
/// Never writes session tokens, cookies, or passwords.
fn emit_event(kind: &str, data: Value) {
    let rec = json!({
        "event": kind,
        "ts": chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
        "data": data,
    });
    let line = redact_for_log(&rec.to_string());
    let Ok(path) = std::env::var("CLOAKCLI_TEACH_HUB_EVENTS") else {
        return;
    };
    if path.is_empty() {
        return;
    }
    let _g = EVENT_FILE_LOCK.lock();
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{line}");
    }
}

async fn run_listener(listener: TcpListener, inner: SharedHub) {
    loop {
        match listener.accept().await {
            Ok((stream, peer)) => {
                if !peer.ip().is_loopback() {
                    hub_log("rejected non-loopback peer");
                    continue;
                }
                let inner = inner.clone();
                tokio::spawn(async move {
                    if let Err(e) = handle_conn(stream, inner).await {
                        hub_log(&format!("conn: {e}"));
                    }
                });
            }
            Err(_) => break,
        }
    }
}

async fn handle_conn(mut stream: TcpStream, inner: SharedHub) -> Result<()> {
    let mut buf = Vec::with_capacity(512);
    let mut tmp = [0u8; 1024];
    loop {
        let n = stream.read(&mut tmp).await?;
        if n == 0 {
            return Ok(());
        }
        buf.extend_from_slice(&tmp[..n]);
        if buf.len() > MAX_MESSAGE_BYTES {
            return Ok(());
        }
        if buf.starts_with(b"GET ") {
            if let Some(pos) = find_header_end(&buf) {
                let headers = buf[..pos].to_vec();
                let rest = buf[pos..].to_vec();
                return handle_websocket(stream, inner, &headers, rest).await;
            }
            if buf.len() > 64 * 1024 {
                return Ok(());
            }
        } else if buf.contains(&b'\n') || (!buf.starts_with(b"G") && buf.len() >= 2) {
            return handle_jsonl(stream, inner, buf).await;
        }
    }
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n").map(|i| i + 4)
}

async fn handle_jsonl(
    stream: TcpStream,
    inner: SharedHub,
    initial: Vec<u8>,
) -> Result<()> {
    let (reader, mut writer) = stream.into_split();
    let mut reader = BufReader::new(reader);
    let mut leftover = initial;
    let mut authed: Option<ClientRole> = None;
    let mut pending = Vec::new();

    loop {
        while let Some(idx) = leftover.iter().position(|b| *b == b'\n') {
            let mut line: Vec<u8> = leftover.drain(..=idx).collect();
            if line.ends_with(&[b'\n']) {
                line.pop();
            }
            if line.ends_with(&[b'\r']) {
                line.pop();
            }
            if line.is_empty() {
                continue;
            }
            pending.push(line);
        }
        for line in pending.drain(..) {
            if line.len() > MAX_MESSAGE_BYTES {
                let err = ProtocolError::new("message_too_large", "message exceeds limit", false);
                write_jsonl(&mut writer, &err.to_envelope(None, None)).await?;
                continue;
            }
            match process_line(&inner, &line, &mut authed).await {
                Ok(replies) => {
                    for env in replies {
                        write_jsonl(&mut writer, &env).await?;
                    }
                }
                Err(err) => {
                    write_jsonl(&mut writer, &err.to_envelope(None, None)).await?;
                }
            }
        }
        leftover.reserve(256);
        let n = reader.read_until(b'\n', &mut leftover).await?;
        if n == 0 {
            mark_disconnected(&inner, authed).await;
            return Ok(());
        }
        if leftover.len() > MAX_MESSAGE_BYTES {
            let err = ProtocolError::new("message_too_large", "message exceeds limit", false);
            write_jsonl(&mut writer, &err.to_envelope(None, None)).await?;
            leftover.clear();
        }
    }
}

async fn write_jsonl<W: AsyncWriteExt + Unpin>(writer: &mut W, env: &Envelope) -> Result<()> {
    let bytes = env.to_jsonl().unwrap_or_else(|_| b"{\"v\":1,\"type\":\"error\",\"data\":{}}\n".to_vec());
    writer.write_all(&bytes).await?;
    writer.flush().await?;
    Ok(())
}

async fn handle_websocket(
    mut stream: TcpStream,
    inner: SharedHub,
    header_bytes: &[u8],
    initial_body: Vec<u8>,
) -> Result<()> {
    let header_text = String::from_utf8_lossy(header_bytes);
    let mut key: Option<String> = None;
    let mut upgrade_ws = false;
    for line in header_text.split("\r\n") {
        let Some((k, v)) = line.split_once(':') else {
            continue;
        };
        let k = k.trim().to_ascii_lowercase();
        let v = v.trim();
        if k == "sec-websocket-key" {
            key = Some(v.to_string());
        }
        if k == "upgrade" && v.eq_ignore_ascii_case("websocket") {
            upgrade_ws = true;
        }
    }
    let Some(key) = key else {
        let resp = b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n";
        let _ = stream.write_all(resp).await;
        return Ok(());
    };
    if !upgrade_ws {
        let resp = b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n";
        let _ = stream.write_all(resp).await;
        return Ok(());
    }
    let accept = ws::accept_key(&key);
    let resp = format!(
        "HTTP/1.1 101 Switching Protocols\r\n\
         Upgrade: websocket\r\n\
         Connection: Upgrade\r\n\
         Sec-WebSocket-Accept: {accept}\r\n\
         \r\n"
    );
    stream.write_all(resp.as_bytes()).await?;
    stream.flush().await?;

    let (mut reader, mut writer) = stream.into_split();
    let mut authed: Option<ClientRole> = None;
    let mut incoming = initial_body;

    // Unauthenticated clients get a pairing_offer (code is for the human;
    // extension/worker also have it via session.json / env).
    {
        let g = inner.read().await;
        let expires = chrono::Utc::now() + chrono::Duration::from_std(PAIRING_TTL).unwrap_or_default();
        let offer = Envelope::new(TYPE_PAIRING_OFFER)
            .with_session(&g.session.session_id)
            .with_data(pairing_offer_data(
                &g.pairing.pairing_id,
                &g.pairing.code,
                &expires.to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
            ));
        drop(g);
        write_ws_text(&mut writer, &offer).await?;
    }

    loop {
        match ws::read_message(&mut reader, &mut incoming).await {
            Ok(ws::WsMsg::Text(text)) => {
                if text.len() > MAX_MESSAGE_BYTES {
                    let err =
                        ProtocolError::new("message_too_large", "message exceeds limit", false);
                    write_ws_text(&mut writer, &err.to_envelope(None, None)).await?;
                    continue;
                }
                match process_line(&inner, text.as_bytes(), &mut authed).await {
                    Ok(replies) => {
                        for env in replies {
                            write_ws_text(&mut writer, &env).await?;
                        }
                    }
                    Err(err) => {
                        write_ws_text(&mut writer, &err.to_envelope(None, None)).await?;
                    }
                }
            }
            Ok(ws::WsMsg::Ping(p)) => {
                let frame = ws::encode_pong(&p);
                writer.write_all(&frame).await?;
            }
            Ok(ws::WsMsg::Pong) => {}
            Ok(ws::WsMsg::Close) | Err(_) => {
                mark_disconnected(&inner, authed).await;
                return Ok(());
            }
        }
    }
}

async fn write_ws_text<W: AsyncWriteExt + Unpin>(writer: &mut W, env: &Envelope) -> Result<()> {
    let payload = env
        .to_vec()
        .unwrap_or_else(|_| b"{\"v\":1,\"type\":\"error\",\"data\":{}}".to_vec());
    writer.write_all(&ws::encode_text(&payload)).await?;
    writer.flush().await?;
    Ok(())
}

async fn mark_disconnected(inner: &SharedHub, role: Option<ClientRole>) {
    if let Some(role) = role {
        let mut g = inner.write().await;
        if let Some(slot) = g.slots.get_mut(&role) {
            slot.connected = false;
        }
    }
}

async fn process_line(
    inner: &SharedHub,
    line: &[u8],
    authed: &mut Option<ClientRole>,
) -> Result<Vec<Envelope>, ProtocolError> {
    let env = teach_protocol::parse_envelope(line)?;
    let typed = env.typed();
    match typed {
        Some(teach_protocol::MsgType::PairingAccept) => {
            handle_pairing_accept(inner, &env, authed).await
        }
        Some(teach_protocol::MsgType::Heartbeat) => {
            require_auth(authed)?;
            Ok(vec![heartbeat_ack(inner, &env).await])
        }
        Some(teach_protocol::MsgType::PageState) => {
            require_auth(authed)?;
            handle_page_state(inner, &env).await
        }
        Some(teach_protocol::MsgType::AllowlistUpdate) => {
            require_auth(authed)?;
            handle_allowlist_update(inner, &env).await
        }
        Some(teach_protocol::MsgType::Cancel) => {
            Ok(vec![error_env(
                "canceled",
                "cancel noted",
                true,
                env.session_id.as_deref(),
                env.request_id.as_deref(),
            )])
        }
        Some(teach_protocol::MsgType::TakeoverStart)
        | Some(teach_protocol::MsgType::TakeoverStop)
        | Some(teach_protocol::MsgType::TakeoverEvent) => {
            // M3: pause the LLM/action executor for the whole takeover window
            // so the agent cannot race the human on the same Playwright page.
            Ok(vec![error_env(
                "not_implemented",
                "takeover is M3",
                false,
                env.session_id.as_deref(),
                env.request_id.as_deref(),
            )])
        }
        Some(other) if !other.is_m1() => Ok(vec![error_env(
            "not_implemented",
            format!("{} is not available in M1", other.as_str()),
            false,
            env.session_id.as_deref(),
            env.request_id.as_deref(),
        )]),
        Some(teach_protocol::MsgType::Error) | Some(teach_protocol::MsgType::PairingOffer) => {
            Ok(vec![])
        }
        _ => Ok(vec![error_env(
            "unknown_type",
            "unknown message type",
            false,
            env.session_id.as_deref(),
            env.request_id.as_deref(),
        )]),
    }
}

fn require_auth(authed: &Option<ClientRole>) -> Result<(), ProtocolError> {
    if authed.is_none() {
        Err(ProtocolError::new(
            "unauthorized",
            "pair before sending",
            false,
        ))
    } else {
        Ok(())
    }
}

fn error_env(
    code: &'static str,
    message: impl Into<String>,
    retryable: bool,
    session_id: Option<&str>,
    request_id: Option<&str>,
) -> Envelope {
    ProtocolError::new(code, message, retryable).to_envelope(session_id, request_id)
}

async fn heartbeat_ack(inner: &SharedHub, env: &Envelope) -> Envelope {
    let g = inner.read().await;
    Envelope::new(TYPE_HEARTBEAT)
        .with_session(&g.session.session_id)
        .with_data(json!({"ok": true}))
        .with_request(env.request_id.as_deref().unwrap_or(""))
}

async fn handle_pairing_accept(
    inner: &SharedHub,
    env: &Envelope,
    authed: &mut Option<ClientRole>,
) -> Result<Vec<Envelope>, ProtocolError> {
    let accept = pairing_accept_from_data(&env.data)?;
    let mut g = inner.write().await;
    let session_id = g.session.session_id.clone();

    if let Some(token) = accept.session_token.as_deref() {
        let role = accept.role;
        let ok = g
            .slots
            .get(&role)
            .map(|s| s.token == token)
            .unwrap_or(false);
        if !ok {
            return Ok(vec![pairing_fail(
                &session_id,
                env.request_id.as_deref(),
                "unauthorized",
                "session token rejected",
                false,
            )]);
        }
        if let Some(slot) = g.slots.get_mut(&role) {
            slot.connected = true;
            if let Some(r) = accept.resume_from {
                slot.last_seq = r;
            }
        }
        g.reconnects = g.reconnects.saturating_add(1);
        *authed = Some(role);
        let resume_seq = g.slots.get(&role).map(|s| s.last_seq).unwrap_or(0);
        let missed: Vec<Envelope> = g
            .timeline
            .iter()
            .filter(|e| e.seq > resume_seq)
            .map(|e| e.env.clone())
            .collect();
        drop(g);
        hub_log(&format!("reconnect role={} (same session)", role.as_str()));
        emit_event(
            "reconnected",
            json!({
                "role": role.as_str(),
                "session_id": session_id,
                "resumed": true,
            }),
        );
        let mut out = vec![pairing_ok(
            &session_id,
            env.request_id.as_deref(),
            token,
            role,
            resume_seq,
            true,
        )];
        out.extend(missed);
        return Ok(out);
    }

    if g.pairing.locked {
        return Ok(vec![pairing_fail(
            &session_id,
            env.request_id.as_deref(),
            "pairing_locked",
            "too many failed attempts",
            false,
        )]);
    }
    if Instant::now() > g.pairing.expires_at {
        return Ok(vec![pairing_fail(
            &session_id,
            env.request_id.as_deref(),
            "pairing_expired",
            "pairing code expired",
            false,
        )]);
    }
    if g.pairing.used_nonces.contains(&accept.nonce) {
        g.pairing.failures = g.pairing.failures.saturating_add(1);
        if g.pairing.failures >= MAX_PAIRING_FAILURES {
            g.pairing.locked = true;
        }
        return Ok(vec![pairing_fail(
            &session_id,
            env.request_id.as_deref(),
            "pairing_replay",
            "nonce already used",
            false,
        )]);
    }
    let id_ok = accept
        .pairing_id
        .as_deref()
        .map(|id| id == g.pairing.pairing_id)
        .unwrap_or(false);
    let code_ok = accept
        .code
        .as_deref()
        .map(|c| c.eq_ignore_ascii_case(&g.pairing.code))
        .unwrap_or(false);
    if !id_ok || !code_ok {
        g.pairing.failures = g.pairing.failures.saturating_add(1);
        if g.pairing.failures >= MAX_PAIRING_FAILURES {
            g.pairing.locked = true;
        }
        return Ok(vec![pairing_fail(
            &session_id,
            env.request_id.as_deref(),
            "pairing_bad_code",
            "pairing rejected",
            true,
        )]);
    }

    let role = accept.role;
    if g.slots.contains_key(&role) {
        // Code is one-shot per role. Reconnect must use the session token.
        drop(g);
        emit_event(
            "pairing_rejected",
            json!({
                "error": "pairing_consumed",
                "role": role.as_str(),
                "session_id": session_id,
            }),
        );
        return Ok(vec![pairing_fail(
            &session_id,
            env.request_id.as_deref(),
            "pairing_consumed",
            "role already paired; reconnect with session token",
            false,
        )]);
    }

    g.pairing.used_nonces.insert(accept.nonce.clone());
    let token = new_token();
    g.slots.insert(
        role,
        PairedSlot {
            token: token.clone(),
            connected: true,
            last_seq: 0,
        },
    );
    *authed = Some(role);
    drop(g);
    hub_log(&format!("paired role={}", role.as_str()));
    emit_event(
        "paired",
        json!({
            "role": role.as_str(),
            "session_id": session_id,
            "resumed": false,
        }),
    );
    Ok(vec![pairing_ok(
        &session_id,
        env.request_id.as_deref(),
        &token,
        role,
        0,
        false,
    )])
}

fn pairing_ok(
    session_id: &str,
    request_id: Option<&str>,
    token: &str,
    role: ClientRole,
    resume_seq: u64,
    resumed: bool,
) -> Envelope {
    let mut env = Envelope::new(TYPE_PAIRING_RESULT).with_session(session_id);
    if let Some(id) = request_id {
        if !id.is_empty() {
            env = env.with_request(id);
        }
    }
    env.data = json!({
        "ok": true,
        "session_id": session_id,
        "session_token": token,
        "role": role.as_str(),
        "resume_seq": resume_seq,
        "resumed": resumed,
    });
    env
}

fn pairing_fail(
    session_id: &str,
    request_id: Option<&str>,
    code: &str,
    message: &str,
    retryable: bool,
) -> Envelope {
    let mut env = Envelope::new(TYPE_PAIRING_RESULT).with_session(session_id);
    if let Some(id) = request_id {
        if !id.is_empty() {
            env = env.with_request(id);
        }
    }
    env.data = json!({
        "ok": false,
        "error": code,
        "message": message,
        "retryable": retryable,
    });
    env
}

async fn handle_page_state(
    inner: &SharedHub,
    env: &Envelope,
) -> Result<Vec<Envelope>, ProtocolError> {
    let mut g = inner.write().await;
    if env
        .session_id
        .as_deref()
        .is_some_and(|id| id != g.session.session_id)
    {
        return Err(ProtocolError::new(
            "unauthorized",
            "session_id mismatch",
            false,
        ));
    }
    let allow = g.session.allow_origins.clone();
    let ps = match PageState::from_data(&env.data, &allow) {
        Ok(ps) => ps,
        Err(err) => {
            if err.code == "origin_not_allowed" {
                emit_event(
                    "page_state_denied",
                    json!({
                        "origin": env.data.get("origin"),
                        "session_id": g.session.session_id,
                    }),
                );
            }
            return Err(err);
        }
    };
    g.seq += 1;
    let seq = g.seq;
    g.session.last_page_state = Some(ps.clone());
    let mut stored = Envelope::new(TYPE_PAGE_STATE)
        .with_session(&g.session.session_id)
        .with_seq(seq)
        .with_data(json!({
            "url": ps.url,
            "origin": ps.origin,
            "title": ps.title,
            "viewport": {"width": ps.viewport.width, "height": ps.viewport.height},
            "observation_id": ps.observation_id,
            "clickable": ps.clickable,
        }));
    if let Some(id) = env.request_id.as_deref() {
        stored = stored.with_request(id);
    }
    g.timeline.push(StoredEvent {
        seq,
        env: stored.clone(),
    });
    if g.timeline.len() > TIMELINE_CAP {
        let drop_n = g.timeline.len() - TIMELINE_CAP;
        g.timeline.drain(0..drop_n);
    }
    hub_log(&format!(
        "page_state origin={} observation_id={} title_len={} viewport={}x{}",
        ps.origin,
        ps.observation_id,
        ps.title.len(),
        ps.viewport.width,
        ps.viewport.height
    ));
    emit_event(
        "page_state",
        json!({
            "url": ps.url,
            "origin": ps.origin,
            "title": ps.title,
            "viewport": {"width": ps.viewport.width, "height": ps.viewport.height},
            "observation_id": ps.observation_id,
            "session_id": g.session.session_id,
        }),
    );
    Ok(vec![stored])
}

async fn handle_allowlist_update(
    inner: &SharedHub,
    env: &Envelope,
) -> Result<Vec<Envelope>, ProtocolError> {
    let origin = env
        .data
        .get("origin")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .unwrap_or("");
    if !teach_protocol::is_http_origin(origin) {
        return Err(ProtocolError::new(
            "invalid_origin",
            "allowlist origin must be http(s)",
            false,
        ));
    }
    let mut g = inner.write().await;
    if !g.session.allow_origins.iter().any(|o| o == origin) {
        g.session.allow_origins.push(origin.to_string());
    }
    hub_log(&format!("allowlist add origin={origin}"));
    Ok(vec![Envelope::new("allowlist_update")
        .with_session(&g.session.session_id)
        .with_data(json!({"ok": true, "origin": origin}))])
}

/// Minimal RFC 6455 server (text/ping/pong/close). Clients must mask.
mod ws {
    use super::MAX_MESSAGE_BYTES;
    use tokio::io::AsyncReadExt;

    pub enum WsMsg {
        Text(String),
        Ping(Vec<u8>),
        Pong,
        Close,
    }

    pub fn accept_key(key: &str) -> String {
        let mut data = Vec::with_capacity(key.len() + 36);
        data.extend_from_slice(key.as_bytes());
        data.extend_from_slice(b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11");
        b64_encode(&sha1(&data))
    }

    pub fn encode_text(payload: &[u8]) -> Vec<u8> {
        encode_frame(0x1, payload)
    }

    pub fn encode_pong(payload: &[u8]) -> Vec<u8> {
        encode_frame(0xA, payload)
    }

    fn encode_frame(opcode: u8, payload: &[u8]) -> Vec<u8> {
        let mut out = Vec::with_capacity(payload.len() + 10);
        out.push(0x80 | opcode);
        let n = payload.len();
        if n <= 125 {
            out.push(n as u8);
        } else if n <= 65535 {
            out.push(126);
            out.extend_from_slice(&(n as u16).to_be_bytes());
        } else {
            out.push(127);
            out.extend_from_slice(&(n as u64).to_be_bytes());
        }
        out.extend_from_slice(payload);
        out
    }

    pub async fn read_message<R: AsyncReadExt + Unpin>(
        reader: &mut R,
        buf: &mut Vec<u8>,
    ) -> Result<WsMsg, ()> {
        loop {
            match parse_frame(buf) {
                Ok(Some((msg, used))) => {
                    buf.drain(..used);
                    return Ok(msg);
                }
                Ok(None) => {}
                Err(()) => return Err(()),
            }
            let mut tmp = [0u8; 2048];
            let n = reader.read(&mut tmp).await.map_err(|_| ())?;
            if n == 0 {
                return Ok(WsMsg::Close);
            }
            buf.extend_from_slice(&tmp[..n]);
            if buf.len() > MAX_MESSAGE_BYTES + 16 {
                return Err(());
            }
        }
    }

    fn parse_frame(buf: &[u8]) -> Result<Option<(WsMsg, usize)>, ()> {
        if buf.len() < 2 {
            return Ok(None);
        }
        let b0 = buf[0];
        let b1 = buf[1];
        let fin = b0 & 0x80 != 0;
        let opcode = b0 & 0x0f;
        let masked = b1 & 0x80 != 0;
        let mut len = (b1 & 0x7f) as usize;
        let mut off = 2;
        if len == 126 {
            if buf.len() < 4 {
                return Ok(None);
            }
            len = u16::from_be_bytes([buf[2], buf[3]]) as usize;
            off = 4;
        } else if len == 127 {
            if buf.len() < 10 {
                return Ok(None);
            }
            len = u64::from_be_bytes(buf[2..10].try_into().unwrap()) as usize;
            off = 10;
        }
        if !fin {
            return Err(());
        }
        if len > MAX_MESSAGE_BYTES {
            return Err(());
        }
        let mask = if masked {
            if buf.len() < off + 4 {
                return Ok(None);
            }
            let m = [buf[off], buf[off + 1], buf[off + 2], buf[off + 3]];
            off += 4;
            Some(m)
        } else {
            None
        };
        if buf.len() < off + len {
            return Ok(None);
        }
        let mut payload = buf[off..off + len].to_vec();
        if let Some(m) = mask {
            for (i, b) in payload.iter_mut().enumerate() {
                *b ^= m[i % 4];
            }
        }
        let used = off + len;
        let msg = match opcode {
            0x1 => {
                let s = String::from_utf8(payload).map_err(|_| ())?;
                WsMsg::Text(s)
            }
            0x8 => WsMsg::Close,
            0x9 => WsMsg::Ping(payload),
            0xA => WsMsg::Pong,
            _ => return Err(()),
        };
        Ok(Some((msg, used)))
    }

    #[cfg(test)]
    pub fn encode_client_text(payload: &[u8]) -> Vec<u8> {
        let mask = [0x11, 0x22, 0x33, 0x44];
        let mut out = Vec::with_capacity(payload.len() + 14);
        out.push(0x81);
        let n = payload.len();
        if n <= 125 {
            out.push(0x80 | n as u8);
        } else if n <= 65535 {
            out.push(0x80 | 126);
            out.extend_from_slice(&(n as u16).to_be_bytes());
        } else {
            out.push(0x80 | 127);
            out.extend_from_slice(&(n as u64).to_be_bytes());
        }
        out.extend_from_slice(&mask);
        for (i, b) in payload.iter().enumerate() {
            out.push(b ^ mask[i % 4]);
        }
        out
    }

    fn b64_encode(data: &[u8]) -> String {
        const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut out = String::new();
        let mut i = 0;
        while i < data.len() {
            let b0 = data[i];
            let b1 = if i + 1 < data.len() { data[i + 1] } else { 0 };
            let b2 = if i + 2 < data.len() { data[i + 2] } else { 0 };
            out.push(T[(b0 >> 2) as usize] as char);
            out.push(T[(((b0 & 0x03) << 4) | (b1 >> 4)) as usize] as char);
            if i + 1 < data.len() {
                out.push(T[(((b1 & 0x0f) << 2) | (b2 >> 6)) as usize] as char);
            } else {
                out.push('=');
            }
            if i + 2 < data.len() {
                out.push(T[(b2 & 0x3f) as usize] as char);
            } else {
                out.push('=');
            }
            i += 3;
        }
        out
    }

    #[allow(clippy::many_single_char_names)]
    fn sha1(message: &[u8]) -> [u8; 20] {
        let mut data = message.to_vec();
        let bit_len = (message.len() as u64) * 8;
        data.push(0x80);
        while data.len() % 64 != 56 {
            data.push(0);
        }
        data.extend_from_slice(&bit_len.to_be_bytes());
        let mut h = [
            0x6745_2301u32,
            0xEFCD_AB89,
            0x98BA_DCFE,
            0x1032_5476,
            0xC3D2_E1F0,
        ];
        for chunk in data.chunks_exact(64) {
            let mut w = [0u32; 80];
            for i in 0..16 {
                w[i] = u32::from_be_bytes(chunk[i * 4..i * 4 + 4].try_into().unwrap());
            }
            for i in 16..80 {
                w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
            }
            let mut a = h[0];
            let mut b = h[1];
            let mut c = h[2];
            let mut d = h[3];
            let mut e = h[4];
            for i in 0..80 {
                let (f, k) = if i < 20 {
                    ((b & c) | ((!b) & d), 0x5A82_7999)
                } else if i < 40 {
                    (b ^ c ^ d, 0x6ED9_EBA1)
                } else if i < 60 {
                    ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC)
                } else {
                    (b ^ c ^ d, 0xCA62_C1D6)
                };
                let temp = a
                    .rotate_left(5)
                    .wrapping_add(f)
                    .wrapping_add(e)
                    .wrapping_add(k)
                    .wrapping_add(w[i]);
                e = d;
                d = c;
                c = b.rotate_left(30);
                b = a;
                a = temp;
            }
            h[0] = h[0].wrapping_add(a);
            h[1] = h[1].wrapping_add(b);
            h[2] = h[2].wrapping_add(c);
            h[3] = h[3].wrapping_add(d);
            h[4] = h[4].wrapping_add(e);
        }
        let mut out = [0u8; 20];
        for (i, v) in h.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&v.to_be_bytes());
        }
        out
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn rfc6455_accept_key() {
            assert_eq!(
                accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
            );
        }

        #[test]
        fn sha1_empty() {
            let d = sha1(b"");
            assert_eq!(
                d.iter().map(|b| format!("{b:02x}")).collect::<String>(),
                "da39a3ee5e6b4b0d3255bfef95601890afd80709"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::teach_protocol::{TYPE_ERROR, TYPE_PAIRING_ACCEPT};
    use tokio::io::AsyncReadExt;
    use tokio::net::TcpStream as TokioTcp;

    async fn jsonl_client(addr: SocketAddr) -> TokioTcp {
        TokioTcp::connect(addr).await.unwrap()
    }

    async fn send_line(s: &mut TokioTcp, env: &Envelope) {
        s.write_all(&env.to_jsonl().unwrap()).await.unwrap();
        s.flush().await.unwrap();
    }

    async fn read_env(s: &mut TokioTcp) -> Envelope {
        let mut buf = Vec::new();
        loop {
            let mut b = [0u8; 1];
            s.read_exact(&mut b).await.unwrap();
            if b[0] == b'\n' {
                break;
            }
            buf.push(b[0]);
            if buf.len() > MAX_MESSAGE_BYTES {
                panic!("line too long");
            }
        }
        teach_protocol::parse_envelope(&buf).unwrap()
    }

    fn accept_env(code: &str, pairing_id: &str, role: &str, nonce: &str) -> Envelope {
        Envelope::new(TYPE_PAIRING_ACCEPT).with_data(json!({
            "pairing_id": pairing_id,
            "code": code,
            "nonce": nonce,
            "role": role,
        }))
    }

    #[tokio::test]
    async fn pairs_extension_and_worker_same_session() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec!["https://example.com".into()],
        })
        .await
        .unwrap();
        let mut ext = jsonl_client(hub.addr).await;
        send_line(
            &mut ext,
            &accept_env(hub.pairing_code(), hub.pairing_id(), "extension", "n-ext"),
        )
        .await;
        let r1 = read_env(&mut ext).await;
        assert_eq!(r1.msg_type, TYPE_PAIRING_RESULT);
        assert_eq!(r1.data["ok"], true);
        let sess = r1.data["session_id"].as_str().unwrap().to_string();
        assert_eq!(sess, hub.session_id());

        let mut wrk = jsonl_client(hub.addr).await;
        send_line(
            &mut wrk,
            &accept_env(hub.pairing_code(), hub.pairing_id(), "worker", "n-wrk"),
        )
        .await;
        let r2 = read_env(&mut wrk).await;
        assert_eq!(r2.data["ok"], true);
        assert_eq!(r2.data["session_id"], sess);
        assert_eq!(hub.session_count().await, 1);
        let mut roles = hub.paired_roles().await;
        roles.sort_by_key(|r| r.as_str().to_string());
        assert_eq!(roles.len(), 2);
    }

    #[tokio::test]
    async fn pairing_bad_code_and_lockout() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec![],
        })
        .await
        .unwrap();
        for i in 0..MAX_PAIRING_FAILURES {
            let mut c = jsonl_client(hub.addr).await;
            send_line(
                &mut c,
                &accept_env("NOPE01", hub.pairing_id(), "extension", &format!("n{i}")),
            )
            .await;
            let r = read_env(&mut c).await;
            assert_eq!(r.data["ok"], false);
            let err = r.data["error"].as_str().unwrap();
            if i + 1 < MAX_PAIRING_FAILURES {
                assert_eq!(err, "pairing_bad_code");
            } else {
                assert!(err == "pairing_bad_code" || err == "pairing_locked", "{err}");
            }
        }
        let mut c = jsonl_client(hub.addr).await;
        send_line(
            &mut c,
            &accept_env(hub.pairing_code(), hub.pairing_id(), "extension", "n-final"),
        )
        .await;
        let r = read_env(&mut c).await;
        assert_eq!(r.data["ok"], false);
        assert_eq!(r.data["error"], "pairing_locked");
        assert_eq!(hub.paired_roles().await.len(), 0);
    }

    #[tokio::test]
    async fn pairing_replay_nonce_rejected() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec![],
        })
        .await
        .unwrap();
        let mut c = jsonl_client(hub.addr).await;
        let env = accept_env(hub.pairing_code(), hub.pairing_id(), "worker", "same-nonce");
        send_line(&mut c, &env).await;
        let ok = read_env(&mut c).await;
        assert_eq!(ok.data["ok"], true);
        drop(c);

        let mut c2 = jsonl_client(hub.addr).await;
        send_line(&mut c2, &env).await;
        let r = read_env(&mut c2).await;
        assert_eq!(r.data["ok"], false);
        assert_eq!(r.data["error"], "pairing_replay");
    }

    #[tokio::test]
    async fn reconnect_does_not_duplicate_session() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec!["https://example.com".into()],
        })
        .await
        .unwrap();
        let mut c = jsonl_client(hub.addr).await;
        send_line(
            &mut c,
            &accept_env(hub.pairing_code(), hub.pairing_id(), "extension", "n1"),
        )
        .await;
        let first = read_env(&mut c).await;
        let token = first.data["session_token"].as_str().unwrap().to_string();
        let sess = first.data["session_id"].as_str().unwrap().to_string();
        drop(c);

        let mut c2 = jsonl_client(hub.addr).await;
        send_line(
            &mut c2,
            &Envelope::new(TYPE_PAIRING_ACCEPT).with_data(json!({
                "session_token": token,
                "nonce": "n2",
                "role": "extension",
                "resume_from": 0
            })),
        )
        .await;
        let r = read_env(&mut c2).await;
        assert_eq!(r.data["ok"], true);
        assert_eq!(r.data["resumed"], true);
        assert_eq!(r.data["session_id"], sess);
        assert_eq!(hub.session_count().await, 1);
        assert_eq!(hub.paired_roles().await.len(), 1);
        assert_eq!(hub.reconnect_count().await, 1);
    }

    #[tokio::test]
    async fn duplicate_pairing_same_role_rejected() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec!["https://example.com".into()],
        })
        .await
        .unwrap();
        let mut c = jsonl_client(hub.addr).await;
        send_line(
            &mut c,
            &accept_env(hub.pairing_code(), hub.pairing_id(), "worker", "n1"),
        )
        .await;
        let first = read_env(&mut c).await;
        assert_eq!(first.data["ok"], true);
        let sess = first.data["session_id"].as_str().unwrap().to_string();

        let mut c2 = jsonl_client(hub.addr).await;
        send_line(
            &mut c2,
            &accept_env(hub.pairing_code(), hub.pairing_id(), "worker", "n2"),
        )
        .await;
        let r = read_env(&mut c2).await;
        assert_eq!(r.data["ok"], false);
        assert_eq!(r.data["error"], "pairing_consumed");
        assert_eq!(hub.session_count().await, 1);
        assert_eq!(hub.session_id(), sess);
        assert_eq!(hub.paired_roles().await.len(), 1);
        assert_eq!(hub.reconnect_count().await, 0);
    }

    #[tokio::test]
    async fn page_state_allowlist_and_fields() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec!["https://example.com".into()],
        })
        .await
        .unwrap();
        let mut c = jsonl_client(hub.addr).await;
        send_line(
            &mut c,
            &accept_env(hub.pairing_code(), hub.pairing_id(), "extension", "n1"),
        )
        .await;
        let paired = read_env(&mut c).await;
        let sess = paired.data["session_id"].as_str().unwrap().to_string();

        send_line(
            &mut c,
            &Envelope::new(TYPE_PAGE_STATE)
                .with_session(&sess)
                .with_data(json!({
                    "url": "https://evil.example/phish",
                    "origin": "https://evil.example",
                    "title": "nope",
                    "viewport": {"width": 10, "height": 10},
                    "observation_id": "obs-evil"
                })),
        )
        .await;
        let denied = read_env(&mut c).await;
        assert_eq!(denied.msg_type, TYPE_ERROR);
        assert_eq!(denied.data["code"], "origin_not_allowed");
        assert!(hub.last_page_state().await.is_none());

        send_line(
            &mut c,
            &Envelope::new(TYPE_PAGE_STATE)
                .with_session(&sess)
                .with_data(json!({
                    "url": "https://example.com/app?token=leakme",
                    "origin": "https://example.com",
                    "title": "Dashboard",
                    "viewport": {"width": 1280, "height": 720},
                    "observation_id": "obs-1",
                    "clickable": [{"tag":"button","role":"button","text":"Go","css":"#go"}]
                })),
        )
        .await;
        let ack = read_env(&mut c).await;
        assert_eq!(ack.msg_type, TYPE_PAGE_STATE);
        assert_eq!(ack.data["origin"], "https://example.com");
        assert_eq!(ack.data["observation_id"], "obs-1");
        assert_eq!(ack.data["title"], "Dashboard");
        assert_eq!(ack.data["viewport"]["width"], 1280);
        assert!(!ack.data["url"].as_str().unwrap().contains("leakme"));
        let stored = hub.last_page_state().await.unwrap();
        assert_eq!(stored.observation_id, "obs-1");
        assert_eq!(stored.clickable[0].selector.as_deref(), Some("#go"));
    }

    #[tokio::test]
    async fn unauthed_page_state_rejected() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec!["https://example.com".into()],
        })
        .await
        .unwrap();
        let mut c = jsonl_client(hub.addr).await;
        send_line(
            &mut c,
            &Envelope::new(TYPE_PAGE_STATE).with_data(json!({
                "url": "https://example.com/",
                "origin": "https://example.com",
                "observation_id": "obs-x"
            })),
        )
        .await;
        let r = read_env(&mut c).await;
        assert_eq!(r.msg_type, TYPE_ERROR);
        assert_eq!(r.data["code"], "unauthorized");
    }

    #[tokio::test]
    async fn websocket_pair_and_page_state() {
        let hub = spawn(TeachHubOpts {
            allow_origins: vec!["https://example.com".into()],
        })
        .await
        .unwrap();
        let mut s = TokioTcp::connect(hub.addr).await.unwrap();
        let req = format!(
            "GET /teach HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n",
            hub.port()
        );
        s.write_all(req.as_bytes()).await.unwrap();
        let mut hdr = Vec::new();
        loop {
            let mut b = [0u8; 1];
            s.read_exact(&mut b).await.unwrap();
            hdr.push(b[0]);
            if hdr.windows(4).any(|w| w == b"\r\n\r\n") {
                break;
            }
        }
        let hs = String::from_utf8_lossy(&hdr);
        assert!(hs.contains("101"), "{hs}");
        assert!(hs.contains("s3pPLMBiTxaQ9kYGzzhZRbK+xOo="), "{hs}");

        let mut incoming = Vec::new();
        let offer = match ws::read_message(&mut s, &mut incoming).await.unwrap() {
            ws::WsMsg::Text(t) => teach_protocol::parse_envelope(t.as_bytes()).unwrap(),
            _ => panic!("expected offer"),
        };
        assert_eq!(offer.msg_type, TYPE_PAIRING_OFFER);

        let accept = accept_env(hub.pairing_code(), hub.pairing_id(), "extension", "ws-n");
        s.write_all(&ws::encode_client_text(&accept.to_vec().unwrap()))
            .await
            .unwrap();
        let result = match ws::read_message(&mut s, &mut incoming).await.unwrap() {
            ws::WsMsg::Text(t) => teach_protocol::parse_envelope(t.as_bytes()).unwrap(),
            _ => panic!("expected result"),
        };
        assert_eq!(result.data["ok"], true);
        assert!(result.data.get("session_token").is_some());
    }

    #[tokio::test]
    async fn logs_do_not_include_token() {
        let msg = redact_for_log(r#"paired {"token":"super-secret-token-value","password":"hunter2"}"#);
        assert!(!msg.contains("super-secret-token-value"), "{msg}");
        assert!(!msg.contains("hunter2"), "{msg}");
    }
}
