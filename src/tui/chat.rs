//! Teach Chat TUI (M2): dialogue, tool-call strip, status, input.
//!
//! Streaming is minimal (one-shot LLM text shown as assistant).
//! Ctrl-T toggles human takeover; Ctrl-R resumes the agent; Ctrl-E exports a skill draft.

use anyhow::Result;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::{Frame, Terminal};
use std::io;
use std::path::Path;
use std::sync::atomic::Ordering;
use std::time::Duration;

use crate::teach_chat::{
    apply_plan, await_cancellable, default_export_name, execute_planned, human_steps_from_values,
    live_llm_from_root, mock_llm_from_env, plan_turn, record_draft_steps, run_llm_turn,
    run_llm_turn_ex, ChatSession, MockLlm, NormalizePending, PageBrief, PlannedTurn, TeachLlm,
};
use crate::teach_hub::TeachHubHandle;
use crate::teach_protocol::TeachMachine;
use uuid::Uuid;

use super::theme::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatCmd {
    None,
    Quit,
    Send,
    Cancel,
    ConfirmYes,
    ConfirmNo,
    Takeover,
    Resume,
    Export,
    ExportCommit,
    ExportCancel,
    ExportOverwrite,
    ScrollUp,
    ScrollDown,
}

pub fn draw(f: &mut Frame, area: Rect, session: &ChatSession, profile: &str, session_id: &str) {
    fill_bg(f, area);
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

fn draw_header(f: &mut Frame, area: Rect, profile: &str, session_id: &str, session: &ChatSession) {
    let sid = if session_id.is_empty() {
        "(local)"
    } else {
        session_id
    };
    let line = Line::from(vec![
        Span::styled(
            " Teach Chat ",
            Style::default().fg(ACCENT).bg(BG).add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!("profile={profile}  session={sid}  "),
            Style::default().fg(MUTED).bg(BG),
        ),
        Span::styled(session.phase.as_str(), Style::default().fg(MUTED).bg(BG)),
    ]);
    f.render_widget(Paragraph::new(line).block(bordered("session", false)), area);
}

fn draw_dialogue(f: &mut Frame, area: Rect, session: &ChatSession) {
    let mut lines: Vec<Line> = Vec::new();
    for m in &session.messages {
        let (tag, color) = match m.role.as_str() {
            "user" => ("User", FG),
            "assistant" => ("Assistant", FG),
            _ => ("System", MUTED),
        };
        lines.push(Line::from(vec![
            Span::styled(
                format!("{tag}: "),
                Style::default().fg(color).bg(BG).add_modifier(Modifier::BOLD),
            ),
            Span::styled(m.text.clone(), Style::default().fg(FG).bg(BG)),
        ]));
    }
    if !session.stream.is_empty() {
        lines.push(Line::from(vec![
            Span::styled(
                "Assistant: ",
                Style::default().fg(FG).bg(BG).add_modifier(Modifier::BOLD),
            ),
            Span::styled(session.stream.clone(), Style::default().fg(MUTED).bg(BG)),
        ]));
    }
    if session.recording {
        lines.push(Line::from(Span::styled(
            "REC: human actions are not exported until you stop takeover (Ctrl-T). They will be normalized to Playwright steps.",
            Style::default().fg(WARN).bg(BG).add_modifier(Modifier::BOLD),
        )));
    }
    if let Some(p) = &session.pending_normalize {
        lines.push(Line::from(Span::styled(
            format!(
                "NORMALIZE: {} step(s) ok, {} need confirm, {} non-exportable. Y=accept proposed, N=drop. Enter does not confirm.",
                p.steps.len(),
                p.needs_confirm,
                p.non_exportable
            ),
            Style::default().fg(WARN).bg(BG).add_modifier(Modifier::BOLD),
        )));
    }
    if let Some(c) = &session.confirm {
        lines.push(Line::from(Span::styled(
            format!(
                "CONFIRM nav {} → {}  (Y=yes, N/Esc=no; Enter does not confirm)",
                c.from_origin, c.to_origin
            ),
            Style::default().fg(WARN).bg(BG).add_modifier(Modifier::BOLD),
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
            Style::default().fg(CHROME).bg(BG),
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
            let tag = if t.summary.starts_with("[HUMAN]") {
                ""
            } else {
                "[LLM] "
            };
            lines.push(Line::from(vec![
                Span::styled(tag, Style::default().fg(CHROME).bg(BG)),
                Span::styled(t.summary.clone(), Style::default().fg(FG).bg(BG)),
                Span::styled(format!("  {}", t.status), Style::default().fg(color).bg(BG)),
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
    let rec = if session.recording { "REC=on" } else { "REC=off" };
    let rec_color = if session.recording { WARN } else { MUTED };
    let line = Line::from(vec![
        Span::styled(format!(" {url} "), Style::default().fg(FG).bg(BG)),
        Span::styled("|", Style::default().fg(BORDER).bg(BG)),
        Span::styled(format!(" {origin} "), Style::default().fg(FG).bg(BG)),
        Span::styled("|", Style::default().fg(BORDER).bg(BG)),
        Span::styled(format!(" {hub} {ext} {wrk} "), Style::default().fg(MUTED).bg(BG)),
        Span::styled("|", Style::default().fg(BORDER).bg(BG)),
        Span::styled(
            format!(" {rec} {} ", session.mode),
            Style::default().fg(rec_color).bg(BG),
        ),
        Span::styled(&session.status, Style::default().fg(MUTED).bg(BG)),
    ]);
    f.render_widget(Paragraph::new(line).block(bordered("status", false)), area);
}

fn draw_input(f: &mut Frame, area: Rect, session: &ChatSession) {
    let hint = if session.pending_normalize.is_some() {
        " Y accept proposed selectors  N drop  (Enter does not confirm) "
    } else if session.phase == TeachMachine::AwaitingConfirm {
        " Y confirm  N/Esc reject  (Enter does not confirm) "
    } else if session.phase == TeachMachine::AgentActing {
        " Ctrl-C cancel  Ctrl-T takeover "
    } else if session.recording {
        " Ctrl-T stop recording  Ctrl-C cancel takeover "
    } else if session.export_overwrite_name.is_some() {
        " skill exists — Y overwrite  N/Esc cancel  (Enter does not overwrite) "
    } else if session.export_prompt {
        " type skill name  Enter export  Esc cancel "
    } else if session.phase == TeachMachine::Resume {
        " Ctrl-R resume agent  Enter send  Ctrl-T takeover  Ctrl-E export "
    } else {
        " Enter send  Ctrl-C cancel  Ctrl-T takeover  Ctrl-R resume  Ctrl-E export "
    };
    let shown = if session.input.is_empty() {
        hint.to_string()
    } else {
        format!(" {}█", session.input)
    };
    let fg = if session.input.is_empty() { CHROME } else { FG };
    f.render_widget(
        Paragraph::new(Span::styled(shown, Style::default().fg(fg).bg(BG)))
            .block(bordered("input", true)),
        area,
    );
}

pub fn handle_key(
    session: &mut ChatSession,
    code: KeyCode,
    mods: KeyModifiers,
) -> ChatCmd {
    if session.export_overwrite_name.is_some() {
        match code {
            KeyCode::Char('y') | KeyCode::Char('Y') => return ChatCmd::ExportOverwrite,
            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => return ChatCmd::ExportCancel,
            KeyCode::Enter => {
                session.status = "Enter does not overwrite — press Y".into();
                return ChatCmd::None;
            }
            KeyCode::Char('c') if mods.contains(KeyModifiers::CONTROL) => return ChatCmd::Cancel,
            _ => return ChatCmd::None,
        }
    }

    if session.export_prompt {
        match code {
            KeyCode::Enter => return ChatCmd::ExportCommit,
            KeyCode::Esc => return ChatCmd::ExportCancel,
            KeyCode::Backspace => {
                session.input.pop();
                return ChatCmd::None;
            }
            KeyCode::Char('c') if mods.contains(KeyModifiers::CONTROL) => return ChatCmd::Cancel,
            KeyCode::Char(c) if !mods.contains(KeyModifiers::CONTROL) => {
                session.input.push(c);
                return ChatCmd::None;
            }
            _ => return ChatCmd::None,
        }
    }

    if session.pending_normalize.is_some() || session.phase == TeachMachine::AwaitingConfirm {
        match code {
            KeyCode::Char('y') | KeyCode::Char('Y') => return ChatCmd::ConfirmYes,
            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => return ChatCmd::ConfirmNo,
            KeyCode::Enter => {
                session.status = "Enter does not confirm — press Y".into();
                return ChatCmd::None;
            }
            KeyCode::Char('c') if mods.contains(KeyModifiers::CONTROL) => return ChatCmd::Cancel,
            KeyCode::Char('t') if mods.contains(KeyModifiers::CONTROL) => return ChatCmd::Takeover,
            _ => return ChatCmd::None,
        }
    }

    if mods.contains(KeyModifiers::CONTROL) {
        return match code {
            KeyCode::Char('c') => ChatCmd::Cancel,
            KeyCode::Char('t') => ChatCmd::Takeover,
            KeyCode::Char('r') => ChatCmd::Resume,
            KeyCode::Char('e') => ChatCmd::Export,
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
        ChatCmd::Takeover => {
            session.recording = !session.recording;
            if session.recording {
                session.phase = TeachMachine::HumanTakeover;
                session.mode = "HUMAN".into();
                session.status = "recording (human takeover)".into();
                session.push_system(
                    "Takeover started. Drive the browser; actions normalize after Ctrl-T stop. Agent is paused.",
                );
            } else {
                session.status = "stopping takeover…".into();
                session.mode = "HUMAN".into();
            }
            false
        }
        ChatCmd::Resume => {
            if session.recording {
                session.status = "stop takeover (Ctrl-T) before resume".into();
                return false;
            }
            session.phase = TeachMachine::Chat;
            session.mode = "LLM".into();
            session.status = "resumed".into();
            session.push_system("Agent resumed from current page + human steps.");
            false
        }
        ChatCmd::Export => {
            if session.recording || session.phase == TeachMachine::HumanTakeover {
                session.status = "stop takeover (Ctrl-T) before export".into();
                session.push_system("stop takeover before exporting a skill draft");
                return false;
            }
            session.export_prompt = true;
            session.export_overwrite_name = None;
            session.phase = TeachMachine::Export;
            session.input = default_export_name(&session.last_goal);
            session.status = "export: edit skill name, Enter to write draft".into();
            session.push_system(
                "Export skill draft: edit the name and press Enter. Esc cancels. Existing skills are not overwritten without Y.",
            );
            false
        }
        ChatCmd::ExportCancel => {
            session.export_prompt = false;
            session.export_overwrite_name = None;
            session.input.clear();
            session.phase = TeachMachine::Chat;
            session.status = "export cancelled".into();
            session.push_system("export cancelled");
            false
        }
        ChatCmd::ExportCommit | ChatCmd::ExportOverwrite => false,
        ChatCmd::Cancel => {
            if session.recording || session.phase == TeachMachine::HumanTakeover {
                session.recording = false;
                session.phase = TeachMachine::Chat;
                session.mode = "LLM".into();
                session.status = "takeover cancelled".into();
                session.push_system("takeover cancelled; events discarded");
                session.pending_normalize = None;
                session.ctrl_c_armed = false;
            } else if session.phase == TeachMachine::AgentActing
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
            if session.pending_normalize.is_some() {
                return false;
            }
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

pub async fn live_send_ex(
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
    let human = if session.last_human_summary.is_empty() {
        None
    } else {
        Some(session.last_human_summary.as_str())
    };
    let planned = run_llm_turn_ex(llm, &goal, page.as_ref(), allow, human).await?;
    session.stream = planned.assistant_text.clone();
    apply_plan(session, &planned);
    Ok(planned)
}

#[allow(dead_code)]
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
        root,
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
    root: &Path,
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
                if session.recording || session.phase == TeachMachine::HumanTakeover {
                    session.push_system("agent paused during takeover — Ctrl-T to stop, Ctrl-R to resume");
                    session.input.clear();
                    continue;
                }
                session.cancel.store(false, Ordering::SeqCst);
                let allow = hub.allow_origins().await;
                let planned = if let Some(m) = mock {
                    let goal = session.input.clone();
                    session.push_user(&goal);
                    session.input.clear();
                    plan_turn(&m.text, &allow, Some(session.page_origin.as_str()).filter(|s| !s.is_empty()))
                } else if let Some(llm) = live {
                    let cancel = session.cancel.clone();
                    await_cancellable(
                        &cancel,
                        live_send_ex(session, llm, &allow),
                        poll_ctrl_c,
                    )
                    .await?
                } else {
                    session.push_system("no LLM configured and no --mock-json / CLOAKCLI_TEACH_CHAT_MOCK");
                    session.phase = TeachMachine::Chat;
                    continue;
                };
                if session.cancel.load(Ordering::SeqCst) {
                    session.phase = TeachMachine::Chat;
                    session.status = "cancelled".into();
                    session.push_system("cancelled in-flight action");
                    pending = None;
                    continue;
                }
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
                if session.pending_normalize.is_some() {
                    match hub.confirm_normalize(true).await {
                        Ok(steps) => {
                            let acts = human_steps_from_values(&steps);
                            record_draft_steps(session, &steps, "human");
                            session.set_human_tools(&acts, "ok");
                            session.pending_normalize = None;
                            session.phase = TeachMachine::Resume;
                            session.status = "human steps confirmed".into();
                            session.last_human_summary = hub.last_human_summary().await;
                            session.push_system(&format!(
                                "accepted proposed selectors; {}",
                                session.last_human_summary
                            ));
                        }
                        Err(e) => session.push_system(&format!("confirm: {e}")),
                    }
                } else if let Some(p) = pending.take() {
                    session.confirm = None;
                    dispatch_now(session, hub, &p, true).await?;
                }
            }
            ChatCmd::ConfirmNo => {
                if session.pending_normalize.is_some() {
                    match hub.confirm_normalize(false).await {
                        Ok(_) => {
                            session.pending_normalize = None;
                            session.phase = TeachMachine::Resume;
                            session.status = "unstable steps dropped (non-exportable)".into();
                            session.last_human_summary = hub.last_human_summary().await;
                            session.push_system("dropped proposed selectors (non-exportable)");
                        }
                        Err(e) => session.push_system(&format!("confirm: {e}")),
                    }
                }
            }
            ChatCmd::Takeover => {
                if session.recording {
                    if session.phase == TeachMachine::AgentActing {
                        if let Some(id) = session.last_request_id.clone() {
                            let _ = hub.cancel_request(Some(&id)).await;
                        }
                        session.cancel.store(true, Ordering::SeqCst);
                    }
                    if let Err(e) = hub.start_takeover("ctrl-t").await {
                        session.recording = false;
                        session.phase = TeachMachine::Chat;
                        session.mode = "LLM".into();
                        session.push_system(&format!("takeover start: {e}"));
                    } else {
                        session.phase = TeachMachine::HumanTakeover;
                        session.mode = "HUMAN".into();
                    }
                } else {
                    match hub.stop_takeover().await {
                        Ok(rx) => match hub
                            .wait_normalize_result(rx, Duration::from_secs(20))
                            .await
                        {
                            Ok(env) => apply_normalize(session, &env.data),
                            Err(e) => {
                                session.phase = TeachMachine::Resume;
                                session.push_system(&format!("normalize: {e}"));
                            }
                        },
                        Err(e) => session.push_system(&format!("takeover stop: {e}")),
                    }
                    session.recording = false;
                    session.mode = "LLM".into();
                }
            }
            ChatCmd::Resume => {
                if let Err(e) = hub.resume_agent().await {
                    session.push_system(&format!("resume: {e}"));
                } else {
                    session.phase = TeachMachine::Chat;
                    session.mode = "LLM".into();
                    session.recording = false;
                }
            }
            ChatCmd::ExportCommit => {
                run_export(session, hub, root, false).await;
            }
            ChatCmd::ExportOverwrite => {
                if let Some(name) = session.export_overwrite_name.clone() {
                    session.input = name;
                }
                run_export(session, hub, root, true).await;
            }
            ChatCmd::Cancel => {
                if let Some(id) = session.last_request_id.clone() {
                    let _ = hub.cancel_request(Some(&id)).await;
                }
                if session.phase == TeachMachine::HumanTakeover || hub.takeover_active().await {
                    let _ = hub.resume_agent().await;
                    session.recording = false;
                    session.mode = "LLM".into();
                    session.phase = TeachMachine::Chat;
                }
            }
            _ => {}
        }
    }
}

async fn run_export(
    session: &mut ChatSession,
    hub: &TeachHubHandle,
    root: &Path,
    overwrite: bool,
) {
    let mut name = session.input.trim().to_string();
    if name.is_empty() {
        name = default_export_name(&session.last_goal);
    }
    let mut steps = hub.exportable_skill_steps().await;
    if steps.is_empty() {
        steps = session.draft_steps.clone();
    }
    if steps.is_empty() {
        session.export_prompt = false;
        session.export_overwrite_name = None;
        session.phase = TeachMachine::Chat;
        session.status = "export failed: no Playwright steps".into();
        session.push_system("no exportable Playwright steps (raw DOM is never exported)");
        hub.note_export(&name, None, false, &["empty steps".into()])
            .await;
        return;
    }
    let goal = if session.last_goal.is_empty() {
        None
    } else {
        Some(session.last_goal.as_str())
    };
    match crate::teach::export_chat_draft(root, &name, goal, &steps, overwrite) {
        Ok(r) => {
            session.export_prompt = false;
            session.export_overwrite_name = None;
            session.input.clear();
            session.phase = TeachMachine::Chat;
            session.status = format!("exported {}", r.path.display());
            session.push_system(&format!(
                "exported {} ({} step(s), agent={}, human={}, params={})",
                r.path.display(),
                r.audit.n_steps,
                r.audit.n_agent,
                r.audit.n_human,
                if r.audit.params.is_empty() {
                    "none".into()
                } else {
                    r.audit.params.join(",")
                }
            ));
            hub.note_export(
                &name,
                Some(&r.path.display().to_string()),
                true,
                &[],
            )
            .await;
        }
        Err(e) => {
            let msg = e.to_string();
            if msg.contains("already exists") && !overwrite {
                session.export_overwrite_name = Some(name);
                session.status = "skill exists — Y overwrite / N cancel".into();
                session.push_system(&msg);
            } else {
                session.export_prompt = false;
                session.export_overwrite_name = None;
                session.phase = TeachMachine::Error;
                session.status = format!("export failed: {msg}");
                session.push_system(&format!("export failed: {msg}"));
                hub.note_export(&name, None, false, &[msg]).await;
            }
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
    let request_id = planned
        .needs_confirm
        .as_ref()
        .map(|c| c.request_id.clone())
        .unwrap_or_else(|| format!("req-{}", Uuid::new_v4()));
    session.last_request_id = Some(request_id.clone());
    let cancel = session.cancel.clone();
    match await_cancellable(
        &cancel,
        execute_planned(hub, planned, &cancel, confirmed, Some(&request_id)),
        poll_ctrl_c,
    )
    .await {
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

fn apply_normalize(session: &mut ChatSession, data: &serde_json::Value) {
    let steps = data
        .get("steps")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let needs = data
        .get("needs_confirm")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let non_exp = data
        .get("non_exportable")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let acts = human_steps_from_values(&steps);
    record_draft_steps(session, &steps, "human");
    session.set_human_tools(&acts, "ok");
    session.last_human_summary = data
        .get("summary")
        .and_then(|s| s.as_str())
        .unwrap_or("")
        .to_string();
    if session.last_human_summary.is_empty() {
        session.last_human_summary = crate::teach_chat::summarize_human(&acts);
    }
    if needs > 0 {
        session.pending_normalize = Some(NormalizePending {
            request_id: data
                .get("request_id")
                .and_then(|s| s.as_str())
                .unwrap_or("normalize")
                .to_string(),
            needs_confirm: needs,
            non_exportable: non_exp,
            steps: acts,
            summary: session.last_human_summary.clone(),
        });
        session.phase = TeachMachine::HumanTakeover;
        session.status = format!("{needs} step(s) need confirm (Y accept / N drop)");
        session.push_system(&format!(
            "normalized {} Playwright step(s), {needs} need confirm, {non_exp} non-exportable",
            session.pending_normalize.as_ref().map(|p| p.steps.len()).unwrap_or(0)
        ));
    } else {
        session.pending_normalize = None;
        session.phase = TeachMachine::Resume;
        session.status = "normalized".into();
        session.push_system(&format!(
            "normalized {} Playwright step(s) source=human. Ctrl-R to resume.",
            acts.len()
        ));
    }
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

/// Non-blocking-ish Ctrl-C poll for in-flight live_send / dispatch.
/// Must not wait long: this is called from `await_cancellable` ticks.
fn poll_ctrl_c() -> bool {
    if !event::poll(Duration::from_millis(0)).unwrap_or(false) {
        return false;
    }
    match event::read() {
        Ok(Event::Key(key)) => {
            key.kind == KeyEventKind::Press
                && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('C'))
                && key.modifiers.contains(KeyModifiers::CONTROL)
        }
        _ => false,
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
    let rec = hub.takeover_active().await;
    if rec {
        session.recording = true;
        session.mode = "HUMAN".into();
        session.takeover_event_count = hub.takeover_event_count().await as u32;
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

    #[test]
    fn poll_ctrl_c_ignores_idle_terminal() {
        // No event queued in tests; must not panic or report a hit.
        assert!(!poll_ctrl_c());
    }

    #[test]
    fn ctrl_t_toggles_takeover_recording() {
        let mut s = ChatSession::default();
        let cmd = handle_key(&mut s, KeyCode::Char('t'), KeyModifiers::CONTROL);
        assert_eq!(cmd, ChatCmd::Takeover);
        assert!(!apply_cmd_local(&mut s, ChatCmd::Takeover));
        assert!(s.recording);
        assert_eq!(s.phase, TeachMachine::HumanTakeover);
        assert_eq!(s.mode, "HUMAN");
        assert!(!apply_cmd_local(&mut s, ChatCmd::Takeover));
        assert!(!s.recording);
    }

    #[test]
    fn ctrl_r_resume_from_takeover_stop() {
        let mut s = ChatSession::default();
        s.phase = TeachMachine::Resume;
        let cmd = handle_key(&mut s, KeyCode::Char('r'), KeyModifiers::CONTROL);
        assert_eq!(cmd, ChatCmd::Resume);
        assert!(!apply_cmd_local(&mut s, ChatCmd::Resume));
        assert_eq!(s.phase, TeachMachine::Chat);
        assert_eq!(s.mode, "LLM");
    }

    #[test]
    fn ctrl_e_opens_export_prompt() {
        let mut s = ChatSession::default();
        s.last_goal = "Sign in".into();
        let cmd = handle_key(&mut s, KeyCode::Char('e'), KeyModifiers::CONTROL);
        assert_eq!(cmd, ChatCmd::Export);
        apply_cmd_local(&mut s, ChatCmd::Export);
        assert!(s.export_prompt);
        assert_eq!(s.phase, TeachMachine::Export);
        assert_eq!(s.input, "sign-in");
        let cmd = handle_key(&mut s, KeyCode::Enter, KeyModifiers::NONE);
        assert_eq!(cmd, ChatCmd::ExportCommit);
        let cmd = handle_key(&mut s, KeyCode::Esc, KeyModifiers::NONE);
        assert_eq!(cmd, ChatCmd::ExportCancel);
    }

    #[test]
    fn apply_normalize_human_steps_not_raw_dom() {
        let mut s = ChatSession::default();
        apply_normalize(
            &mut s,
            &serde_json::json!({
                "steps": [
                    {"action":"click","selector":"#ok","source":"human"},
                    {"kind":"click","selector":"#raw"}
                ],
                "needs_confirm": [],
                "non_exportable": [{"reason":"shadow_dom"}],
                "summary": "click #ok"
            }),
        );
        assert_eq!(s.phase, TeachMachine::Resume);
        assert!(s.tools.iter().any(|t| t.summary.contains("[HUMAN]")));
        assert!(!s.last_human_summary.contains("kind"));
    }
}
