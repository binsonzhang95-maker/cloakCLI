//! Teach Chat TUI (M2): dialogue, tool-call strip, status, input.
//!
//! Streaming is minimal (one-shot LLM text shown as assistant). Ctrl-T/R/E
//! are stubs toward M3/M4.

use anyhow::Result;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Paragraph, Wrap};
use ratatui::{Frame, Terminal};
use std::io;
use std::path::Path;
use std::sync::atomic::Ordering;
use std::time::Duration;

use crate::teach_chat::{
    apply_plan, execute_planned, live_llm_from_root, mock_llm_from_env, plan_turn, run_llm_turn,
    stub_shortcut, ChatSession, MockLlm, PageBrief, PlannedTurn, TeachLlm,
};
use crate::teach_hub::TeachHubHandle;
use crate::teach_protocol::TeachMachine;

const BG: Color = Color::Rgb(22, 22, 24);
const FG: Color = Color::Rgb(230, 230, 230);
const MUTED: Color = Color::Rgb(120, 120, 128);
const ACCENT: Color = Color::Rgb(217, 119, 87);
const INFO: Color = Color::Rgb(96, 165, 250);
const OK: Color = Color::Rgb(74, 222, 128);
const WARN: Color = Color::Rgb(251, 191, 36);
const ERR: Color = Color::Rgb(248, 113, 113);
const PURPLE: Color = Color::Rgb(167, 139, 250);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatCmd {
    None,
    Quit,
    Send,
    Cancel,
    ConfirmYes,
    ConfirmNo,
    StubT,
    StubR,
    StubE,
    ScrollUp,
    ScrollDown,
}

pub fn draw(f: &mut Frame, area: Rect, session: &ChatSession, profile: &str, session_id: &str) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(6),
            Constraint::Length(3),
            Constraint::Length(3),
        ])
        .split(area);

    draw_header(f, chunks[0], profile, session_id, session);

    let mid = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(62), Constraint::Percentage(38)])
        .split(chunks[1]);
    draw_dialogue(f, mid[0], session);
    draw_tools(f, mid[1], session);
    draw_status_bar(f, chunks[2], session);
    draw_input(f, chunks[3], session);
}

fn bordered(title: &str, focused: bool) -> Block<'static> {
    Block::bordered()
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(if focused { ACCENT } else { MUTED }))
        .title(Span::styled(
            format!(" {title} "),
            Style::default().fg(if focused { ACCENT } else { MUTED }),
        ))
        .style(Style::default().bg(BG).fg(FG))
}

fn draw_header(f: &mut Frame, area: Rect, profile: &str, session_id: &str, session: &ChatSession) {
    let sid = if session_id.is_empty() {
        "(local)"
    } else {
        session_id
    };
    let line = Line::from(vec![
        Span::styled(" Teach Chat ", Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)),
        Span::styled(format!("profile={profile}  session={sid}  "), Style::default().fg(MUTED)),
        Span::styled(session.phase.as_str(), Style::default().fg(INFO)),
    ]);
    f.render_widget(Paragraph::new(line).block(bordered("session", false)), area);
}

fn draw_dialogue(f: &mut Frame, area: Rect, session: &ChatSession) {
    let mut lines: Vec<Line> = Vec::new();
    for m in &session.messages {
        let (tag, color) = match m.role.as_str() {
            "user" => ("User", ACCENT),
            "assistant" => ("Assistant", INFO),
            _ => ("System", MUTED),
        };
        lines.push(Line::from(vec![
            Span::styled(format!("{tag}: "), Style::default().fg(color).add_modifier(Modifier::BOLD)),
            Span::styled(m.text.clone(), Style::default().fg(FG)),
        ]));
    }
    if !session.stream.is_empty() {
        lines.push(Line::from(vec![
            Span::styled("Assistant: ", Style::default().fg(INFO).add_modifier(Modifier::BOLD)),
            Span::styled(session.stream.clone(), Style::default().fg(MUTED)),
        ]));
    }
    if let Some(c) = &session.confirm {
        lines.push(Line::from(Span::styled(
            format!(
                "CONFIRM nav {} → {}  (Y=yes, N/Esc=no; Enter does not confirm)",
                c.from_origin, c.to_origin
            ),
            Style::default().fg(WARN).add_modifier(Modifier::BOLD),
        )));
    }
    let inner = area.height.saturating_sub(2) as usize;
    let skip = session.scroll as usize;
    if lines.len() > inner && skip < lines.len() {
        let start = lines.len().saturating_sub(inner).saturating_sub(skip);
        lines = lines[start..].to_vec();
    }
    f.render_widget(
        Paragraph::new(lines)
            .wrap(Wrap { trim: true })
            .block(bordered("dialogue", true)),
        area,
    );
}

fn draw_tools(f: &mut Frame, area: Rect, session: &ChatSession) {
    let mut lines: Vec<Line> = Vec::new();
    if session.tools.is_empty() {
        lines.push(Line::from(Span::styled(
            "(no actions this turn)",
            Style::default().fg(MUTED),
        )));
    } else {
        for t in &session.tools {
            let color = match t.status.as_str() {
                "ok" | "done" | "validated" => OK,
                "fail" | "rejected" | "error" => ERR,
                "cancelled" => WARN,
                "needs_confirm" => WARN,
                "running" | "pending" => INFO,
                _ => FG,
            };
            lines.push(Line::from(vec![
                Span::styled("[LLM] ", Style::default().fg(PURPLE)),
                Span::styled(t.summary.clone(), Style::default().fg(FG)),
                Span::styled(format!("  {}", t.status), Style::default().fg(color)),
            ]));
        }
    }
    f.render_widget(
        Paragraph::new(lines)
            .wrap(Wrap { trim: true })
            .block(bordered("tools / actions", false)),
        area,
    );
}

fn draw_status_bar(f: &mut Frame, area: Rect, session: &ChatSession) {
    let url = if session.page_url.is_empty() {
        "-"
    } else {
        &session.page_url
    };
    let origin = if session.page_origin.is_empty() {
        "-"
    } else {
        &session.page_origin
    };
    let hub = if session.hub_connected { "hub=ok" } else { "hub=-" };
    let ext = if session.extension_connected {
        "ext=ok"
    } else {
        "ext=-"
    };
    let wrk = if session.worker_connected {
        "worker=ok"
    } else {
        "worker=-"
    };
    let rec = "REC=off";
    let line = Line::from(vec![
        Span::styled(format!(" {url} "), Style::default().fg(INFO)),
        Span::styled("|", Style::default().fg(MUTED)),
        Span::styled(format!(" {origin} "), Style::default().fg(FG)),
        Span::styled("|", Style::default().fg(MUTED)),
        Span::styled(format!(" {hub} {ext} {wrk} "), Style::default().fg(MUTED)),
        Span::styled("|", Style::default().fg(MUTED)),
        Span::styled(format!(" {rec} {} ", session.mode), Style::default().fg(ACCENT)),
        Span::styled(&session.status, Style::default().fg(MUTED)),
    ]);
    f.render_widget(Paragraph::new(line).block(bordered("status", false)), area);
}

fn draw_input(f: &mut Frame, area: Rect, session: &ChatSession) {
    let hint = if session.phase == TeachMachine::AwaitingConfirm {
        " Y confirm  N/Esc reject  (Enter does not confirm) "
    } else if session.phase == TeachMachine::AgentActing {
        " Ctrl-C cancel in-flight action "
    } else {
        " Enter send  Ctrl-C cancel  Ctrl-T/R/E stub  Esc clear "
    };
    let shown = if session.input.is_empty() {
        hint.to_string()
    } else {
        format!(" {}█", session.input)
    };
    let fg = if session.input.is_empty() { MUTED } else { FG };
    f.render_widget(
        Paragraph::new(Span::styled(shown, Style::default().fg(fg))).block(bordered("input", true)),
        area,
    );
}

pub fn handle_key(
    session: &mut ChatSession,
    code: KeyCode,
    mods: KeyModifiers,
) -> ChatCmd {
    if session.phase == TeachMachine::AwaitingConfirm {
        match code {
            KeyCode::Char('y') | KeyCode::Char('Y') => return ChatCmd::ConfirmYes,
            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => return ChatCmd::ConfirmNo,
            KeyCode::Enter => {
                session.status = "Enter does not confirm high-risk navigation — press Y".into();
                return ChatCmd::None;
            }
            KeyCode::Char('c') if mods.contains(KeyModifiers::CONTROL) => return ChatCmd::Cancel,
            _ => return ChatCmd::None,
        }
    }

    if mods.contains(KeyModifiers::CONTROL) {
        return match code {
            KeyCode::Char('c') => ChatCmd::Cancel,
            KeyCode::Char('t') => ChatCmd::StubT,
            KeyCode::Char('r') => ChatCmd::StubR,
            KeyCode::Char('e') => ChatCmd::StubE,
            KeyCode::Char('q') => ChatCmd::Quit,
            _ => ChatCmd::None,
        };
    }

    match code {
        KeyCode::Enter => {
            if session.input.trim().is_empty() {
                ChatCmd::None
            } else {
                ChatCmd::Send
            }
        }
        KeyCode::Esc => {
            session.input.clear();
            session.ctrl_c_armed = false;
            ChatCmd::None
        }
        KeyCode::Backspace => {
            session.input.pop();
            ChatCmd::None
        }
        KeyCode::PageUp => ChatCmd::ScrollUp,
        KeyCode::PageDown => ChatCmd::ScrollDown,
        KeyCode::Char(c) => {
            session.input.push(c);
            session.ctrl_c_armed = false;
            ChatCmd::None
        }
        _ => ChatCmd::None,
    }
}

pub fn apply_cmd_local(session: &mut ChatSession, cmd: ChatCmd) -> bool {
    match cmd {
        ChatCmd::Quit => true,
        ChatCmd::ScrollUp => {
            session.scroll = session.scroll.saturating_add(1);
            false
        }
        ChatCmd::ScrollDown => {
            session.scroll = session.scroll.saturating_sub(1);
            false
        }
        ChatCmd::StubT => {
            session.push_system(stub_shortcut('t'));
            session.status = stub_shortcut('t').into();
            false
        }
        ChatCmd::StubR => {
            session.push_system(stub_shortcut('r'));
            session.status = stub_shortcut('r').into();
            false
        }
        ChatCmd::StubE => {
            session.push_system(stub_shortcut('e'));
            session.status = stub_shortcut('e').into();
            false
        }
        ChatCmd::Cancel => {
            if session.phase == TeachMachine::AgentActing
                || session.phase == TeachMachine::AwaitingConfirm
            {
                session.cancel.store(true, Ordering::SeqCst);
                session.phase = TeachMachine::Cancel;
                session.confirm = None;
                session.status = "cancelled".into();
                session.push_system("cancelled in-flight action");
                session.ctrl_c_armed = false;
            } else if session.ctrl_c_armed {
                return true;
            } else {
                session.ctrl_c_armed = true;
                session.status = "Ctrl-C again to leave Teach Chat".into();
            }
            false
        }
        ChatCmd::ConfirmNo => {
            session.confirm = None;
            session.phase = TeachMachine::Chat;
            session.status = "navigation rejected".into();
            session.push_system("high-risk navigation rejected");
            false
        }
        _ => false,
    }
}

/// Dry-run a user send against mock/live LLM (no worker execute).
pub fn dry_send(session: &mut ChatSession, llm_text: &str, allow: &[String]) -> PlannedTurn {
    let goal = std::mem::take(&mut session.input);
    session.push_user(&goal);
    session.phase = TeachMachine::AgentActing;
    session.status = "validating".into();
    let current = if session.page_origin.is_empty() {
        None
    } else {
        Some(session.page_origin.as_str())
    };
    let planned = plan_turn(llm_text, allow, current);
    apply_plan(session, &planned);
    planned
}

pub async fn live_send(
    session: &mut ChatSession,
    llm: &dyn TeachLlm,
    allow: &[String],
) -> Result<PlannedTurn> {
    let goal = std::mem::take(&mut session.input);
    session.push_user(&goal);
    session.phase = TeachMachine::AgentActing;
    session.status = "llm…".into();
    session.cancel.store(false, Ordering::SeqCst);
    let page = if session.page_url.is_empty() {
        None
    } else {
        Some(PageBrief {
            url: session.page_url.clone(),
            origin: session.page_origin.clone(),
            title: session.page_title.clone(),
        })
    };
    let planned = run_llm_turn(llm, &goal, page.as_ref(), allow).await?;
    session.stream = planned.assistant_text.clone();
    apply_plan(session, &planned);
    Ok(planned)
}

pub struct DedicatedOpts {
    pub profile: String,
    pub mock_json: Option<String>,
}

/// Full-screen Teach Chat (used by `cloakcli teach chat`).
pub async fn run_dedicated(
    root: &Path,
    hub: TeachHubHandle,
    opts: DedicatedOpts,
) -> Result<()> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut session = ChatSession::default();
    session.hub_connected = true;
    let mock = opts
        .mock_json
        .clone()
        .map(|t| MockLlm { text: t })
        .or_else(mock_llm_from_env);
    let live = if mock.is_none() {
        live_llm_from_root(root).ok()
    } else {
        None
    };

    let result = dedicated_loop(
        &mut terminal,
        &mut session,
        &hub,
        &opts.profile,
        mock.as_ref(),
        live.as_ref(),
    )
    .await;

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    result
}

async fn dedicated_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    session: &mut ChatSession,
    hub: &TeachHubHandle,
    profile: &str,
    mock: Option<&MockLlm>,
    live: Option<&crate::teach_chat::LiveLlm>,
) -> Result<()> {
    let mut pending: Option<PlannedTurn> = None;
    loop {
        refresh_from_hub(session, hub).await;
        terminal.draw(|f| draw(f, f.area(), session, profile, hub.session_id()))?;
        if !event::poll(Duration::from_millis(120))? {
            continue;
        }
        let Event::Key(key) = event::read()? else {
            continue;
        };
        if key.kind != KeyEventKind::Press {
            continue;
        }
        let cmd = handle_key(session, key.code, key.modifiers);
        if apply_cmd_local(session, cmd) {
            return Ok(());
        }
        match cmd {
            ChatCmd::Send => {
                let allow = hub.allow_origins().await;
                let planned = if let Some(m) = mock {
                    let goal = session.input.clone();
                    session.push_user(&goal);
                    session.input.clear();
                    plan_turn(&m.text, &allow, Some(session.page_origin.as_str()).filter(|s| !s.is_empty()))
                } else if let Some(llm) = live {
                    live_send(session, llm, &allow).await?
                } else {
                    session.push_system("no LLM configured and no --mock-json / CLOAKCLI_TEACH_CHAT_MOCK");
                    session.phase = TeachMachine::Chat;
                    continue;
                };
                if mock.is_some() {
                    apply_plan(session, &planned);
                }
                if planned.needs_confirm.is_some() {
                    pending = Some(planned);
                    continue;
                }
                if planned.errors.is_empty() && !planned.actions.is_empty() {
                    dispatch_now(session, hub, &planned, false).await?;
                }
                pending = None;
            }
            ChatCmd::ConfirmYes => {
                if let Some(p) = pending.take() {
                    session.confirm = None;
                    dispatch_now(session, hub, &p, true).await?;
                }
            }
            ChatCmd::Cancel => {
                if let Some(id) = session.last_request_id.clone() {
                    let _ = hub.cancel_request(Some(&id)).await;
                }
            }
            _ => {}
        }
    }
}

async fn dispatch_now(
    session: &mut ChatSession,
    hub: &TeachHubHandle,
    planned: &PlannedTurn,
    confirmed: bool,
) -> Result<()> {
    session.phase = TeachMachine::AgentActing;
    session.status = "executing".into();
    for t in session.tools.iter_mut() {
        t.status = "running".into();
    }
    session.cancel.store(false, Ordering::SeqCst);
    match execute_planned(hub, planned, &session.cancel, confirmed).await {
        Ok(v) => {
            let cancelled = v.get("cancelled").and_then(|x| x.as_bool()).unwrap_or(false);
            if cancelled {
                session.phase = TeachMachine::Chat;
                session.status = "cancelled".into();
                for t in session.tools.iter_mut() {
                    t.status = "cancelled".into();
                }
            } else {
                session.phase = TeachMachine::Chat;
                session.status = "done".into();
                apply_results(session, v.get("data").unwrap_or(&v));
            }
        }
        Err(e) => {
            session.phase = TeachMachine::Error;
            session.status = e.to_string();
            session.push_system(&format!("execute: {e}"));
        }
    }
    Ok(())
}

fn apply_results(session: &mut ChatSession, data: &serde_json::Value) {
    if let Some(arr) = data.get("results").and_then(|v| v.as_array()) {
        for (i, r) in arr.iter().enumerate() {
            let st = r.get("status").and_then(|s| s.as_str()).unwrap_or("?");
            if let Some(t) = session.tools.get_mut(i) {
                t.status = st.to_string();
            }
            if let Some(page) = r.get("page") {
                if let Some(u) = page.get("url").and_then(|x| x.as_str()) {
                    session.page_url = u.to_string();
                }
                if let Some(o) = page.get("origin").and_then(|x| x.as_str()) {
                    session.page_origin = o.to_string();
                }
                if let Some(t) = page.get("title").and_then(|x| x.as_str()) {
                    session.page_title = t.to_string();
                }
            }
        }
        session.push_system(&format!("results: {} step(s)", arr.len()));
    }
}

async fn refresh_from_hub(session: &mut ChatSession, hub: &TeachHubHandle) {
    let (ext, wrk) = hub.connection_status().await;
    session.extension_connected = ext;
    session.worker_connected = wrk;
    session.hub_connected = true;
    if let Some((url, origin, title)) = hub.last_page_brief().await {
        session.page_url = url;
        session.page_origin = origin;
        session.page_title = title;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::teach_chat::ChatSession;

    #[test]
    fn enter_on_empty_does_not_send() {
        let mut s = ChatSession::default();
        let cmd = handle_key(&mut s, KeyCode::Enter, KeyModifiers::NONE);
        assert_eq!(cmd, ChatCmd::None);
        s.input = "click submit".into();
        let cmd = handle_key(&mut s, KeyCode::Enter, KeyModifiers::NONE);
        assert_eq!(cmd, ChatCmd::Send);
    }

    #[test]
    fn confirm_rejects_enter() {
        let mut s = ChatSession::default();
        s.phase = TeachMachine::AwaitingConfirm;
        s.confirm = Some(crate::teach_chat::NavConfirm {
            request_id: "r".into(),
            from_origin: "https://a.example".into(),
            to_url: "https://b.example/".into(),
            to_origin: "https://b.example".into(),
            actions: vec![],
        });
        let cmd = handle_key(&mut s, KeyCode::Enter, KeyModifiers::NONE);
        assert_eq!(cmd, ChatCmd::None);
        let cmd = handle_key(&mut s, KeyCode::Char('y'), KeyModifiers::NONE);
        assert_eq!(cmd, ChatCmd::ConfirmYes);
    }

    #[test]
    fn ctrl_c_cancels_then_quit() {
        let mut s = ChatSession::default();
        s.phase = TeachMachine::AgentActing;
        let cmd = handle_key(&mut s, KeyCode::Char('c'), KeyModifiers::CONTROL);
        assert_eq!(cmd, ChatCmd::Cancel);
        assert!(!apply_cmd_local(&mut s, ChatCmd::Cancel));
        s.phase = TeachMachine::Chat;
        assert!(!apply_cmd_local(&mut s, ChatCmd::Cancel));
        assert!(s.ctrl_c_armed);
        assert!(apply_cmd_local(&mut s, ChatCmd::Cancel));
    }
}
