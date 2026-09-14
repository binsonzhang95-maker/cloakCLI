//! TUI rendering — near-black canvas, white body text, sparse coral accents.

use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{
    Block, BorderType, Clear, List, ListItem, Paragraph, Tabs, Wrap,
};
use ratatui::Frame;

use crate::profiles;
use crate::util::redact_proxy;

use super::theme::*;
use super::{App, InputMode, Tab};

/// Lightweight warm-orange highlight that sweeps across `text`.
/// Brightness + bold only — no invert, no hue flash, no full-screen scroll.
fn shimmer_spans(text: &str, phase: u32, base_style: Style) -> Vec<Span<'static>> {
    let chars: Vec<char> = text.chars().collect();
    if chars.is_empty() {
        return Vec::new();
    }
    let n = chars.len();
    let period = (n + 3).max(1);
    let head = (phase as usize) % period;
    let band = 2usize;
    let peak = shimmer_highlight_style(base_style);

    let mut out: Vec<Span<'static>> = Vec::new();
    let mut buf = String::new();
    let mut cur: Option<Style> = None;
    for (i, ch) in chars.into_iter().enumerate() {
        let style = if i >= head && i < head.saturating_add(band) {
            peak
        } else {
            base_style
        };
        match cur {
            Some(s) if s == style => buf.push(ch),
            Some(s) => {
                out.push(Span::styled(std::mem::take(&mut buf), s));
                buf.push(ch);
                cur = Some(style);
            }
            None => {
                buf.push(ch);
                cur = Some(style);
            }
        }
    }
    if let Some(s) = cur {
        if !buf.is_empty() {
            out.push(Span::styled(buf, s));
        }
    }
    out
}

fn shimmer_highlight_style(base: Style) -> Style {
    let fg = match base.fg {
        Some(Color::White) => ACCENT_HOT,
        Some(Color::Rgb(r, g, b)) if r >= 200 && g >= 200 && b >= 200 => ACCENT_HOT,
        Some(Color::Rgb(r, g, b)) => Color::Rgb(
            r.saturating_add(38).min(255),
            g.saturating_add(48).min(255),
            b.saturating_add(36).min(255),
        ),
        Some(c) => c,
        None => ACCENT_HOT,
    };
    base.fg(fg).add_modifier(Modifier::BOLD)
}

/// Selected list row: accent ▸ + shimmering white name+detail (no inverted block).
/// Unselected: static white.
fn list_row_spans(selected: bool, phase: u32, anim: bool, text: &str) -> Vec<Span<'static>> {
    let marker = if selected {
        Span::styled(
            "▸ ",
            Style::default()
                .fg(ACCENT)
                .bg(BG)
                .add_modifier(Modifier::BOLD),
        )
    } else {
        Span::styled("  ", Style::default().fg(FG).bg(BG))
    };
    let base = Style::default().fg(FG).bg(BG);
    let mut spans = vec![marker];
    if selected && anim {
        spans.extend(shimmer_spans(text, phase, base));
    } else if selected {
        spans.push(Span::styled(text.to_string(), base.add_modifier(Modifier::BOLD)));
    } else {
        spans.push(Span::styled(text.to_string(), base));
    }
    spans
}

fn shimmer_list_item(selected: bool, phase: u32, anim: bool, text: impl AsRef<str>) -> ListItem<'static> {
    ListItem::new(Line::from(list_row_spans(
        selected,
        phase,
        anim,
        text.as_ref(),
    )))
}

/// ASCII `| / - \` spinner via throbber-widgets-tui (ratatui 0.28). Static `[busy]` when off.
fn draw_throbber(app: &App) -> Vec<Span<'static>> {
    let Some(kind) = app.busy else {
        return Vec::new();
    };
    let label = if app.status.is_empty() {
        kind.label().to_string()
    } else {
        app.status.clone()
    };
    let mut spans = vec![Span::styled(" ", Style::default().bg(BG))];
    if app.animations_enabled {
        let throb = throbber_widgets_tui::Throbber::default()
            .throbber_set(throbber_widgets_tui::ASCII)
            .use_type(throbber_widgets_tui::WhichUse::Spin)
            .throbber_style(
                Style::default()
                    .fg(ACCENT)
                    .bg(BG)
                    .add_modifier(Modifier::BOLD),
            );
        let sym = throb.to_symbol_span(&app.throbber_state);
        spans.push(Span::styled(
            sym.content.to_string(),
            Style::default()
                .fg(ACCENT)
                .bg(BG)
                .add_modifier(Modifier::BOLD),
        ));
        spans.extend(shimmer_spans(
            &label,
            app.animation_phase,
            Style::default().fg(ACCENT).bg(BG),
        ));
    } else {
        spans.push(Span::styled(
            "[busy] ",
            Style::default()
                .fg(ACCENT)
                .bg(BG)
                .add_modifier(Modifier::BOLD),
        ));
        spans.push(Span::styled(label, Style::default().fg(FG).bg(BG)));
    }
    spans
}

fn throbber_symbol(app: &App) -> Span<'static> {
    if !app.animations_enabled {
        return Span::styled(
            "[busy]",
            Style::default()
                .fg(ACCENT)
                .bg(BG)
                .add_modifier(Modifier::BOLD),
        );
    }
    let throb = throbber_widgets_tui::Throbber::default()
        .throbber_set(throbber_widgets_tui::ASCII)
        .use_type(throbber_widgets_tui::WhichUse::Spin)
        .throbber_style(
            Style::default()
                .fg(ACCENT)
                .bg(BG)
                .add_modifier(Modifier::BOLD),
        );
    let sym = throb.to_symbol_span(&app.throbber_state);
    Span::styled(
        sym.content.to_string(),
        Style::default()
            .fg(ACCENT)
            .bg(BG)
            .add_modifier(Modifier::BOLD),
    )
}

pub fn draw(f: &mut Frame, app: &mut App) {
    let area = f.area();
    // Full-frame BG every draw so Terminal.app light theme cannot bleed through.
    fill_bg(f, area);

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3), // header
            Constraint::Length(3), // tabs
            Constraint::Min(8),    // main
            Constraint::Length(3), // footer help
            Constraint::Length(1), // status
        ])
        .split(area);

    draw_top_bar(f, chunks[0], app);
    draw_tabs(f, chunks[1], app);
    draw_main(f, chunks[2], app);
    draw_footer(f, chunks[3], app);
    draw_status(f, chunks[4], app);

    if app.input_mode.is_some() {
        draw_input_modal(f, area, app);
    }
}

fn draw_top_bar(f: &mut Frame, area: Rect, app: &App) {
    let headed_pill = if app.headed {
        pill("HEADED", WARN)
    } else {
        pill("HEADLESS", CHROME)
    };
    let conc = Span::styled(
        format!(" conc:{} ", app.concurrency),
        Style::default().fg(CHROME).bg(BG),
    );
    let stub = pill("DEV STUB", PURPLE);
    let hub_ok = app.hub_bind_ok;
    let hub_span = if hub_ok {
        pill(format!("hub {}", app.hub_bind), INFO)
    } else {
        pill(format!("hub {}?", app.hub_bind), WARN)
    };

    let brand_style = Style::default()
        .fg(ACCENT)
        .bg(BG)
        .add_modifier(Modifier::BOLD);
    let mut spans = vec![Span::styled(" ", Style::default().bg(BG))];
    if app.animations_enabled {
        spans.extend(shimmer_spans("CloakCLI", app.animation_phase, brand_style));
    } else {
        spans.push(Span::styled("CloakCLI", brand_style));
    }
    spans.push(Span::styled(" ", Style::default().bg(BG)));
    spans.push(Span::styled(
        format!("v{} ", env!("CARGO_PKG_VERSION")),
        Style::default().fg(CHROME).bg(BG),
    ));
    spans.push(stub);
    spans.push(Span::styled("  ", Style::default().bg(BG)));
    spans.push(headed_pill);
    spans.push(Span::styled(" ", Style::default().bg(BG)));
    spans.push(conc);
    spans.push(Span::styled("  ", Style::default().bg(BG)));
    spans.push(hub_span);
    spans.push(Span::styled("  │ ", Style::default().fg(BORDER).bg(BG)));
    spans.push(Span::styled(
        "master control plane",
        Style::default().fg(CHROME).bg(BG),
    ));
    if app.busy.is_some() {
        spans.push(Span::styled(" ", Style::default().bg(BG)));
        spans.push(throbber_symbol(app));
    }

    let p = Paragraph::new(Line::from(spans)).block(
        Block::bordered()
            .border_type(BorderType::Double)
            .border_style(Style::default().fg(BORDER))
            .style(Style::default().bg(BG).fg(FG)),
    );
    f.render_widget(p, area);
}

fn draw_tabs(f: &mut Frame, area: Rect, app: &App) {
    let titles: Vec<Line> = Tab::all()
        .iter()
        .enumerate()
        .map(|(i, t)| {
            let label = format!("{} {}", i + 1, t.title_bilingual());
            if *t == app.tab {
                let mut spans = vec![Span::styled(" ", style_selected())];
                if app.animations_enabled {
                    spans.extend(shimmer_spans(label.as_str(), app.animation_phase, style_selected()));
                } else {
                    spans.push(Span::styled(label, style_selected()));
                }
                spans.push(Span::styled(" ", style_selected()));
                Line::from(spans)
            } else {
                Line::from(Span::styled(
                    format!(" {label} "),
                    Style::default().fg(CHROME).bg(BG),
                ))
            }
        })
        .collect();

    let tabs = Tabs::new(titles)
        .select(app.tab as usize)
        .divider(Span::styled("│", Style::default().fg(BORDER).bg(BG)))
        .block(
            Block::bordered()
                .border_type(BorderType::Rounded)
                .border_style(Style::default().fg(BORDER))
                .title(Span::styled(" panes ", Style::default().fg(CHROME).bg(BG)))
                .style(Style::default().bg(BG).fg(FG)),
        )
        .highlight_style(Style::default()); // already styled per-title
    f.render_widget(tabs, area);
}

fn draw_main(f: &mut Frame, area: Rect, app: &mut App) {
    match app.tab {
        Tab::Config => draw_config(f, area, app),
        Tab::Logs => draw_logs(f, area, app),
        Tab::Chat => {
            let profile = app
                .selected_profile_name()
                .unwrap_or_else(|| "(none)".into());
            super::chat::draw(f, area, &app.chat, &profile, "");
        }
        _ => draw_split_list_detail(f, area, app),
    }
}

fn draw_split_list_detail(f: &mut Frame, area: Rect, app: &mut App) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(60), Constraint::Percentage(40)])
        .split(area);

    draw_list_pane(f, cols[0], app);
    draw_detail_pane(f, cols[1], app);
}

fn draw_list_pane(f: &mut Frame, area: Rect, app: &mut App) {
    let (title, empty_hint) = match app.tab {
        Tab::Profiles => ("Profiles / 配置", "no profiles — press n to create"),
        Tab::Skills => ("Skills / 技能", "no skills — add under skills/*/skill.json"),
        Tab::Sessions => ("Sessions / 会话", "no local browser sessions"),
        Tab::Clients => ("Clients / 节点", "no clients — cloakcli client connect …"),
        _ => ("", ""),
    };

    let real_len = match app.tab {
        Tab::Profiles => app.profile_rows.len(),
        Tab::Skills => app.skill_rows.len(),
        Tab::Sessions => app.session_rows.len(),
        Tab::Clients => app.client_rows.len(),
        _ => 0,
    };
    {
        let state = match app.tab {
            Tab::Profiles => &mut app.profile_state,
            Tab::Skills => &mut app.skill_state,
            Tab::Sessions => &mut app.session_state,
            Tab::Clients => &mut app.client_state,
            _ => {
                let list = List::new(Vec::<ListItem>::new()).block(bordered(title, true));
                f.render_widget(list, area);
                return;
            }
        };
        if real_len > 0 && state.selected().is_none() {
            state.select(Some(0));
        }
        if real_len == 0 {
            state.select(None);
        }
    }

    let selected = match app.tab {
        Tab::Profiles => app.profile_state.selected(),
        Tab::Skills => app.skill_state.selected(),
        Tab::Sessions => app.session_state.selected(),
        Tab::Clients => app.client_state.selected(),
        _ => None,
    };
    let phase = app.animation_phase;
    let anim = app.animations_enabled;

    let items: Vec<ListItem> = match app.tab {
        Tab::Profiles => {
            if app.profile_rows.is_empty() {
                vec![empty_item(empty_hint)]
            } else {
                app.profile_rows
                    .iter()
                    .enumerate()
                    .map(|(i, p)| {
                        let proxy = profiles::display_proxy(p);
                        let ck = app
                            .cookie_summaries
                            .get(&p.name)
                            .cloned()
                            .unwrap_or_else(|| "none".into());
                        let text = format!(
                            "{:<16} proxy:{:<18} [{}]",
                            truncate(&p.name, 16),
                            truncate(&proxy, 18),
                            truncate(&ck, 24)
                        );
                        shimmer_list_item(selected == Some(i), phase, anim, text)
                    })
                    .collect()
            }
        }
        Tab::Skills => {
            if app.skill_rows.is_empty() {
                vec![empty_item(empty_hint)]
            } else {
                app.skill_rows
                    .iter()
                    .enumerate()
                    .map(|(i, s)| {
                        let text = format!(
                            "{:<16} {}",
                            truncate(&s.name, 16),
                            truncate(&s.description, 48)
                        );
                        shimmer_list_item(selected == Some(i), phase, anim, text)
                    })
                    .collect()
            }
        }
        Tab::Sessions => {
            if app.session_rows.is_empty() {
                vec![empty_item(
                    app.sessions_hint.as_deref().unwrap_or(empty_hint),
                )]
            } else {
                app.session_rows
                    .iter()
                    .enumerate()
                    .map(|(i, s)| {
                        let headed = if s.headed { "H" } else { "h" };
                        let text = format!(
                            "{:<10} {:<12} [{headed}] {}",
                            truncate(&s.id, 10),
                            truncate(&s.profile, 12),
                            truncate(&s.url, 36)
                        );
                        shimmer_list_item(selected == Some(i), phase, anim, text)
                    })
                    .collect()
            }
        }
        Tab::Clients => {
            if app.client_rows.is_empty() {
                vec![empty_item(empty_hint)]
            } else {
                app.client_rows
                    .iter()
                    .enumerate()
                    .map(|(i, c)| {
                        let age = c.last_seen.elapsed().as_secs();
                        let badge = if c.online { "ONLINE " } else { "offline" };
                        let job = c
                            .last_job_state
                            .as_ref()
                            .and_then(|v| v.get("state"))
                            .and_then(|s| s.as_str())
                            .unwrap_or("-");
                        let text = format!(
                            "{:<12} {badge} seen={age}s rev={} job={job}",
                            truncate(&c.client_id, 12),
                            c.observed.revision
                        );
                        shimmer_list_item(selected == Some(i), phase, anim, text)
                    })
                    .collect()
            }
        }
        _ => vec![],
    };

    let state = match app.tab {
        Tab::Profiles => &mut app.profile_state,
        Tab::Skills => &mut app.skill_state,
        Tab::Sessions => &mut app.session_state,
        Tab::Clients => &mut app.client_state,
        _ => {
            let list = List::new(items).block(bordered(title, true));
            f.render_widget(list, area);
            return;
        }
    };

    // Do not use highlight_style — ratatui's default REVERSED / style_selected
    // paints a solid block that washes out the 流光. Selection is in the row.
    let list = List::new(items)
        .block(bordered(title, true))
        .highlight_style(Style::default());
    f.render_stateful_widget(list, area, state);
}

fn empty_item(hint: &str) -> ListItem<'static> {
    ListItem::new(Line::from(Span::styled(
        format!("  ({hint})"),
        Style::default()
            .fg(CHROME)
            .bg(BG)
            .add_modifier(Modifier::ITALIC),
    )))
}

fn draw_detail_pane(f: &mut Frame, area: Rect, app: &App) {
    let lines = match app.tab {
        Tab::Profiles => detail_profile(app),
        Tab::Skills => detail_skill(app),
        Tab::Sessions => detail_session(app),
        Tab::Clients => detail_client(app),
        _ => vec![Line::from("")],
    };

    let p = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .block(bordered("Detail / 详情", false))
        .style(style_normal());
    f.render_widget(p, area);
}

fn kv(key: &str, val: impl AsRef<str>, val_style: Style) -> Line<'static> {
    let val_style = if val_style.bg.is_none() {
        val_style.bg(BG)
    } else {
        val_style
    };
    Line::from(vec![
        Span::styled(
            format!("  {:<14}", format!("{key}:")),
            Style::default().fg(MUTED).bg(BG),
        ),
        Span::styled(val.as_ref().to_string(), val_style),
    ])
}

fn detail_profile(app: &App) -> Vec<Line<'static>> {
    let Some(i) = app.profile_state.selected() else {
        return vec![hint_line("select a profile")];
    };
    let Some(p) = app.profile_rows.get(i) else {
        return vec![hint_line("select a profile")];
    };
    let proxy = p
        .proxy
        .as_ref()
        .map(|s| redact_proxy(s))
        .unwrap_or_else(|| "(none)".into());
    let ck = app
        .cookie_summaries
        .get(&p.name)
        .cloned()
        .unwrap_or_else(|| "none".into());
    let notes = p.notes.clone().unwrap_or_else(|| "-".into());
    vec![
        Line::from(Span::styled(
            format!("  {}", p.name),
            Style::default()
                .fg(FG)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        kv("proxy", proxy, Style::default().fg(FG)),
        kv(
            "cookies",
            ck,
            Style::default().fg(COOKIE),
        ),
        kv("notes", notes, Style::default().fg(FG)),
        kv(
            "user_data",
            truncate(&p.user_data_dir, 42),
            Style::default().fg(MUTED),
        ),
        kv(
            "created",
            truncate(&p.created_at, 32),
            Style::default().fg(MUTED),
        ),
        Line::from(""),
        Line::from(Span::styled(
            "  chips: proxy redacted · cookie status only",
            Style::default().fg(CHROME).add_modifier(Modifier::ITALIC),
        )),
    ]
}

fn detail_skill(app: &App) -> Vec<Line<'static>> {
    let Some(i) = app.skill_state.selected() else {
        return vec![hint_line("select a skill")];
    };
    let Some(s) = app.skill_rows.get(i) else {
        return vec![hint_line("select a skill")];
    };
    let steps = s.steps.len();
    let params = s.params.len();
    vec![
        Line::from(Span::styled(
            format!("  {}", s.name),
            Style::default()
                .fg(FG)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        kv(
            "description",
            if s.description.is_empty() {
                "-".into()
            } else {
                s.description.clone()
            },
            Style::default().fg(FG),
        ),
        kv(
            "schema",
            s.schema_version.to_string(),
            Style::default().fg(MUTED),
        ),
        kv("steps", steps.to_string(), Style::default().fg(FG)),
        kv("params", params.to_string(), Style::default().fg(MUTED)),
        kv(
            "path",
            truncate(&s.path.to_string_lossy(), 42),
            Style::default().fg(MUTED),
        ),
        Line::from(""),
        Line::from(Span::styled(
            "  Enter = run on selected profile (local)",
            Style::default().fg(CHROME).add_modifier(Modifier::ITALIC),
        )),
    ]
}

fn detail_session(app: &App) -> Vec<Line<'static>> {
    if app.session_rows.is_empty() {
        return vec![hint_line(
            app.sessions_hint
                .as_deref()
                .unwrap_or("no open sessions"),
        )];
    }
    let Some(i) = app.session_state.selected() else {
        return vec![hint_line("select a session")];
    };
    let Some(s) = app.session_rows.get(i) else {
        return vec![hint_line("select a session")];
    };
    vec![
        Line::from(Span::styled(
            format!("  session {}", s.id),
            Style::default()
                .fg(FG)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        kv("id", s.id.clone(), Style::default().fg(FG)),
        kv("profile", s.profile.clone(), Style::default().fg(FG)),
        kv(
            "headed",
            if s.headed { "yes" } else { "no" },
            Style::default().fg(if s.headed { WARN } else { MUTED }),
        ),
        kv("url", s.url.clone(), Style::default().fg(FG)),
        Line::from(""),
        Line::from(Span::styled(
            "  x = close selected (or all if none)",
            Style::default().fg(CHROME).add_modifier(Modifier::ITALIC),
        )),
    ]
}

fn detail_client(app: &App) -> Vec<Line<'static>> {
    if app.client_rows.is_empty() {
        return vec![
            hint_line("no remote clients connected"),
            Line::from(""),
            Line::from(Span::styled(
                "  cloakcli client connect \\",
                Style::default().fg(CHROME),
            )),
            Line::from(Span::styled(
                format!("    --master {} --id box1", app.hub_bind),
                Style::default().fg(CHROME),
            )),
        ];
    }
    let Some(i) = app.client_state.selected() else {
        return vec![hint_line("select a client")];
    };
    let Some(c) = app.client_rows.get(i) else {
        return vec![hint_line("select a client")];
    };
    let age = c.last_seen.elapsed().as_secs();
    let job = c
        .last_job_state
        .as_ref()
        .map(|v| v.to_string())
        .unwrap_or_else(|| "-".into());
    let job_short = truncate(&job, 48);
    vec![
        Line::from(Span::styled(
            format!("  {}", c.client_id),
            Style::default()
                .fg(FG)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        kv(
            "status",
            if c.online { "ONLINE" } else { "offline" },
            Style::default().fg(if c.online { OK } else { MUTED }),
        ),
        kv(
            "last_seen",
            format!("{age}s ago"),
            Style::default().fg(MUTED),
        ),
        kv(
            "obs_rev",
            c.observed.revision.to_string(),
            Style::default().fg(FG),
        ),
        kv("job", job_short, Style::default().fg(WARN)),
        Line::from(""),
        Line::from(Span::styled(
            "  J = submit job (selected skill@profile)",
            Style::default().fg(CHROME).add_modifier(Modifier::ITALIC),
        )),
    ]
}

fn hint_line(msg: &str) -> Line<'static> {
    Line::from(Span::styled(
        format!("  ({msg})"),
        Style::default()
            .fg(CHROME)
            .bg(BG)
            .add_modifier(Modifier::ITALIC),
    ))
}

fn draw_config(f: &mut Frame, area: Rect, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(14), Constraint::Length(10)])
        .split(area);

    let saved_model = if app.llm.model.is_empty() {
        "(none)".to_string()
    } else {
        app.llm.model.clone()
    };
    let base_shown = if app.llm_draft_base_url.is_empty() {
        app.llm.base_url.clone()
    } else {
        app.llm_draft_base_url.clone()
    };
    let env_name = if app.llm.api_key_env.is_empty() {
        crate::llm::DEFAULT_API_KEY_ENV.to_string()
    } else {
        app.llm.api_key_env.clone()
    };
    let key_label = if app.llm.key_present {
        if app.llm_session_key_set {
            "session env set (not on disk)"
        } else {
            "set"
        }
    } else {
        "missing"
    };

    let mut lines: Vec<Line> = vec![
        Line::from(Span::styled(
            "  Runtime defaults",
            Style::default()
                .fg(FG)
                .bg(BG)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        kv(
            "headed",
            if app.headed { "true" } else { "false" },
            Style::default().fg(if app.headed { WARN } else { OK }).bg(BG),
        ),
        kv(
            "concurrency",
            app.concurrency.to_string(),
            Style::default().fg(FG).bg(BG),
        ),
        kv("hub bind", app.hub_bind.clone(), Style::default().fg(FG).bg(BG)),
        Line::from(""),
        Line::from(Span::styled(
            "  LLM (OpenAI-compatible · key strategy A)",
            Style::default()
                .fg(FG)
                .bg(BG)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        kv(
            "enabled [l]",
            if app.llm.enabled { "true" } else { "false" },
            Style::default().fg(if app.llm.enabled { OK } else { MUTED }),
        ),
        kv(
            "saved model",
            truncate(&saved_model, 40),
            Style::default().fg(FG).bg(BG),
        ),
        kv(
            "base_url [b]",
            truncate(&base_shown, 42),
            Style::default().fg(FG).bg(BG),
        ),
        kv(
            "api_key [K]",
            format!("********  {key_label} · env {env_name}"),
            Style::default().fg(if app.llm.key_present { OK } else { WARN }),
        ),
        kv(
            "recover_timeout [t]",
            format!("{}s (default 90)", app.llm.recover_timeout_sec),
            Style::default().fg(FG),
        ),
        kv(
            "max_model_rounds",
            if app.llm.max_model_rounds == 0 {
                "3".into()
            } else {
                app.llm.max_model_rounds.to_string()
            },
            Style::default().fg(FG),
        ),
        kv(
            "teach_smart_optimize",
            if app.llm.teach_smart_optimize { "on" } else { "off" },
            Style::default().fg(FG),
        ),
    ];
    if let Some(err) = &app.llm_fetch_err {
        lines.push(kv(
            "fetch",
            truncate(err, 48),
            Style::default().fg(ERR),
        ));
    }
    lines.push(Line::from(Span::styled(
        "  Key is env-only (never saved). Failed fetch does not change saved model.",
        Style::default().fg(CHROME).add_modifier(Modifier::ITALIC),
    )));
    let p = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .block(bordered("Config / 配置", true))
        .style(style_normal());
    f.render_widget(p, chunks[0]);

    if !app.llm_models.is_empty() && app.llm_model_state.selected().is_none() {
        app.llm_model_state.select(Some(0));
    }
    if app.llm_models.is_empty() {
        app.llm_model_state.select(None);
    }
    let selected = app.llm_model_state.selected();
    let items: Vec<ListItem> = if app.llm_models.is_empty() {
        vec![empty_item(
            "f fetch models — GET {base}/models; Enter saves selected id",
        )]
    } else {
        app.llm_models
            .iter()
            .enumerate()
            .map(|(i, id)| {
                let mark = if *id == app.llm.model { " *" } else { "" };
                shimmer_list_item(
                    selected == Some(i),
                    app.animation_phase,
                    app.animations_enabled,
                    format!("{id}{mark}"),
                )
            })
            .collect()
    };
    let title = if app.llm_models_truncated {
        format!("Models / 模型  ({} truncated)", app.llm_models.len())
    } else {
        format!("Models / 模型  ({})", app.llm_models.len())
    };
    let list = List::new(items)
        .highlight_style(Style::default())
        .block(bordered(&title, true));
    f.render_stateful_widget(list, chunks[1], &mut app.llm_model_state);
}

fn draw_logs(f: &mut Frame, area: Rect, app: &App) {
    let max_show = area.height.saturating_sub(2) as usize;
    let start = app.logs.len().saturating_sub(max_show.max(1));
    let lines: Vec<Line> = app.logs[start..]
        .iter()
        .map(|l| {
            let style = if l.contains("FAIL") || l.contains("ERR") || l.contains("error") {
                Style::default().fg(ERR).bg(BG)
            } else if l.contains("OK") || l.contains("created") || l.contains("submitted") {
                Style::default().fg(OK).bg(BG)
            } else if l.contains("WARN") || l.contains("INVALID") {
                Style::default().fg(WARN).bg(BG)
            } else {
                Style::default().fg(FG).bg(BG)
            };
            Line::from(Span::styled(l.clone(), style))
        })
        .collect();

    let title = format!("Logs / 日志  ({} entries, latest at bottom)", app.logs.len());
    let p = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .block(bordered(&title, true))
        .style(style_normal());
    f.render_widget(p, area);
}

fn draw_footer(f: &mut Frame, area: Rect, app: &App) {
    let mut spans = vec![Span::styled(" ", style_desc())];
    spans.extend(context_help(app.tab));
    let p = Paragraph::new(Line::from(spans)).block(
        Block::bordered()
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(BORDER))
            .style(Style::default().bg(BG).fg(FG)),
    );
    f.render_widget(p, area);
}

fn help_bits(pairs: &[(&'static str, &'static str)]) -> Vec<Span<'static>> {
    let mut out = Vec::new();
    for (i, (k, d)) in pairs.iter().enumerate() {
        if i > 0 {
            out.push(Span::styled(" · ", style_desc()));
        }
        out.push(Span::styled(*k, style_key()));
        if !d.is_empty() {
            out.push(Span::styled(format!(" {d}"), style_desc()));
        }
    }
    out
}

fn context_help(tab: Tab) -> Vec<Span<'static>> {
    let common = help_bits(&[
        ("Tab/1-7", "panes"),
        ("jk/↑↓", ""),
        ("h", "headed"),
        ("c/[ ]", "conc"),
        ("r", "reload"),
        ("q", "quit"),
    ]);
    let mut spans = match tab {
        Tab::Profiles => help_bits(&[
            ("n", "new"),
            ("e", "proxy"),
            ("i/E/C", "cookies"),
            ("o", "open"),
            ("T", "teach"),
            ("Enter", "run skill"),
        ]),
        Tab::Skills => help_bits(&[
            ("Enter", "run on selected profile (local)"),
            ("T", "teach on selected profile"),
        ]),
        Tab::Sessions => help_bits(&[("x", "close session"), ("o", "open from Profiles")]),
        Tab::Clients => help_bits(&[("J", "submit remote job (skill@profile)")]),
        Tab::Config => help_bits(&[
            ("b", "base_url"),
            ("K", "session key"),
            ("f", "fetch models"),
            ("Enter", "save model"),
            ("t", "timeout"),
            ("l", "toggle llm"),
        ]),
        Tab::Logs => {
            let mut s = vec![Span::styled("auto-refresh live", style_desc())];
            s.push(Span::styled(" · ", style_desc()));
            s.extend(help_bits(&[("r", "reload lists")]));
            s
        }
        Tab::Chat => help_bits(&[
            ("Enter", "send"),
            ("Ctrl-C", "cancel"),
            ("Y/N", "confirm nav"),
            ("Ctrl-T", "takeover"),
            ("Ctrl-R", "resume"),
            ("Ctrl-E", "export draft"),
        ]),
    };
    spans.push(Span::styled("  │  ", style_desc()));
    spans.extend(common);
    spans
}

fn draw_status(f: &mut Frame, area: Rect, app: &App) {
    if app.busy.is_some() {
        let p = Paragraph::new(Line::from(draw_throbber(app)))
            .alignment(Alignment::Left)
            .style(Style::default().bg(BG).fg(ACCENT));
        f.render_widget(p, area);
        return;
    }
    let (fg, bg) = status_colors(&app.status);
    let text = format!(" {} ", app.status);
    let p = Paragraph::new(Span::styled(
        text,
        Style::default().fg(fg).bg(bg).add_modifier(Modifier::BOLD),
    ))
    .alignment(Alignment::Left)
    .style(Style::default().bg(bg));
    f.render_widget(p, area);
}

fn status_colors(status: &str) -> (Color, Color) {
    let s = status.to_lowercase();
    if s.contains("fail") || s.contains("err") || s.contains("error") {
        (ERR, STATUS_ERR_BG)
    } else if s.contains("cancel") || s.contains("warn") {
        (WARN, STATUS_WARN_BG)
    } else if s == "ready" || s == "ok" || s.contains("submitted") || s.contains("created") {
        (OK, STATUS_OK_BG)
    } else {
        (FG, BG)
    }
}

fn draw_input_modal(f: &mut Frame, area: Rect, app: &App) {
    let mode = match &app.input_mode {
        Some(m) => m,
        None => return,
    };
    let (title, hint) = match mode {
        InputMode::NewProfile => ("New profile", "name → Enter · Esc cancel"),
        InputMode::EditProxy => (
            "Edit proxy",
            "URL (empty=clear) → Enter · Esc cancel · credentials stored, redacted in UI",
        ),
        InputMode::CookieImport => (
            "Import cookies",
            "path to storage_state / cookies-json → Enter · Esc cancel",
        ),
        InputMode::CookieExport => (
            "Export cookies",
            "output path (required) → Enter · Esc cancel · mode 0600",
        ),
        InputMode::LlmBaseUrl => (
            "LLM base_url",
            "http(s) OpenAI-compatible root (single /v1) → Enter · Esc cancel",
        ),
        InputMode::LlmApiKey => (
            "LLM API key (masked)",
            "sets session env only — never saved to llm.json or logs → Enter · Esc cancel",
        ),
        InputMode::LlmTimeout => (
            "LLM recover_timeout_sec",
            "default 90 (60–120 form; 300 advanced) · 5..=3600 → Enter · Esc cancel",
        ),
    };

    let modal = centered_rect(64, 28, area);
    f.render_widget(Clear, modal);

    let shown = match mode {
        InputMode::LlmApiKey => format!("{}█", "*".repeat(app.input_buf.chars().count())),
        _ => format!("{}█", app.input_buf),
    };
    let body = vec![
        Line::from(""),
        Line::from(vec![
            Span::styled(
                "  > ",
                Style::default().fg(ACCENT).bg(SURFACE).add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                shown,
                Style::default()
                    .fg(FG)
                    .bg(SURFACE)
                    .add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(""),
        Line::from(Span::styled(
            format!("  {hint}"),
            Style::default().fg(CHROME).bg(SURFACE),
        )),
    ];

    let block = Block::bordered()
        .border_type(BorderType::Double)
        .border_style(Style::default().fg(BORDER_FOCUS))
        .title(Line::from(vec![
            Span::styled(
                " ▸ ",
                Style::default()
                    .fg(ACCENT)
                    .bg(SURFACE)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("{title} "),
                Style::default()
                    .fg(FG)
                    .bg(SURFACE)
                    .add_modifier(Modifier::BOLD),
            ),
        ]))
        .title_bottom(Span::styled(
            " Esc cancel ",
            Style::default().fg(CHROME).bg(SURFACE),
        ))
        .style(Style::default().bg(SURFACE).fg(FG));

    let p = Paragraph::new(body).block(block);
    f.render_widget(p, modal);
}

fn centered_rect(percent_x: u16, percent_y: u16, r: Rect) -> Rect {
    let popup = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(r);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup[1])[1]
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else if max <= 1 {
        "…".into()
    } else {
        let t: String = s.chars().take(max - 1).collect();
        format!("{t}…")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn collect_text(spans: &[Span<'_>]) -> String {
        spans.iter().map(|s| s.content.as_ref()).collect()
    }

    #[test]
    fn shimmer_spans_preserve_text() {
        let style = Style::default().fg(ACCENT);
        let spans = shimmer_spans("CloakCLI", 0, style);
        assert_eq!(collect_text(&spans), "CloakCLI");
        assert!(!spans.is_empty());
        for s in &spans {
            assert!(!s.style.add_modifier.contains(Modifier::REVERSED));
            assert!(!s.style.sub_modifier.contains(Modifier::REVERSED));
        }
    }

    #[test]
    fn shimmer_highlight_moves_with_phase() {
        let style = Style::default().fg(ACCENT);
        let a = shimmer_spans("CloakCLI", 0, style);
        let b = shimmer_spans("CloakCLI", 3, style);
        assert_eq!(collect_text(&a), collect_text(&b));
        // Peak style should sit on different characters as phase advances.
        let peak = shimmer_highlight_style(style);
        let peak_a: String = a
            .iter()
            .filter(|s| s.style.fg == peak.fg)
            .map(|s| s.content.as_ref())
            .collect();
        let peak_b: String = b
            .iter()
            .filter(|s| s.style.fg == peak.fg)
            .map(|s| s.content.as_ref())
            .collect();
        assert_ne!(peak_a, peak_b);
    }

    #[test]
    fn shimmer_empty_text() {
        assert!(shimmer_spans("", 4, Style::default()).is_empty());
    }

    #[test]
    fn ascii_throbber_set_is_pipe_slash_dash_backslash() {
        assert_eq!(throbber_widgets_tui::ASCII.symbols, &["|", "/", "-", "\\"]);
    }

    #[test]
    fn white_shimmer_peak_is_accent_hot() {
        let base = Style::default().fg(FG).bg(BG);
        let peak = shimmer_highlight_style(base);
        assert_eq!(peak.fg, Some(ACCENT_HOT));
        assert!(peak.add_modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn selected_list_row_shimmers_without_block() {
        let spans = list_row_spans(true, 0, true, "demo proxy:- [none]");
        assert_eq!(collect_text(&spans), "▸ demo proxy:- [none]");
        assert_eq!(spans[0].content.as_ref(), "▸ ");
        assert_eq!(spans[0].style.fg, Some(ACCENT));
        assert_ne!(spans[0].style.bg, Some(ACCENT));
        for s in &spans {
            assert_ne!(s.style.bg, Some(ACCENT), "selection must not wash out with accent block");
            assert!(!s.style.add_modifier.contains(Modifier::REVERSED));
        }
        let peak = shimmer_highlight_style(Style::default().fg(FG).bg(BG));
        assert!(
            spans.iter().any(|s| s.style.fg == peak.fg),
            "selected row should carry the shimmer peak"
        );
    }

    #[test]
    fn unselected_list_row_is_static_white() {
        let spans = list_row_spans(false, 0, true, "demo proxy:- [none]");
        assert_eq!(collect_text(&spans), "  demo proxy:- [none]");
        for s in &spans {
            assert_eq!(s.style.fg, Some(FG));
            assert_eq!(s.style.bg, Some(BG));
            assert_ne!(s.style.fg, Some(ACCENT_HOT));
        }
    }
}
