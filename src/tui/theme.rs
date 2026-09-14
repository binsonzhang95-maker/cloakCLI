//! Shared TUI palette: ANSI-black canvas, white body text, sparse accents.
//!
//! `BG`/`FG` use indexed ANSI colors so Terminal.app (and GNU screen without
//! truecolor) still paints a real black canvas. Bright colors are only for
//! brand shimmer, key hints, the active tab marker, and status pills — never
//! default borders or body text. List selection is a 流光 shimmer on white,
//! not a solid inverted block.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType};
use ratatui::Frame;

/// ANSI 0 — reliable black even when truecolor / Rgb delivery fails.
pub const BG: Color = Color::Black;
pub const SURFACE: Color = Color::Rgb(18, 18, 22);
/// ANSI 7 — reliable white body text without truecolor.
pub const FG: Color = Color::White;
/// Near-white secondary text (labels, proxy/domains). Never dim ~110 grey.
pub const MUTED: Color = Color::Rgb(210, 210, 214);
/// Decorative chrome only (inactive titles, version, footer hints).
pub const CHROME: Color = Color::Rgb(168, 168, 176);
/// Unfocused pane chrome — dark gray, never an accent.
pub const BORDER: Color = Color::Rgb(42, 42, 48);
/// Focused pane chrome — slightly lifted gray, still not an accent.
pub const BORDER_FOCUS: Color = Color::Rgb(72, 72, 80);
pub const ACCENT: Color = Color::Rgb(217, 119, 87); // coral — brand / keys / selected marker
pub const ACCENT_HOT: Color = Color::Rgb(255, 176, 148); // shimmer peak
pub const INFO: Color = Color::Rgb(96, 165, 250);
pub const PURPLE: Color = Color::Rgb(167, 139, 250);
pub const OK: Color = Color::Rgb(74, 222, 128);
pub const WARN: Color = Color::Rgb(251, 191, 36);
pub const ERR: Color = Color::Rgb(248, 113, 113);
/// Dark fg on accent / colored pills.
pub const ON_PILL: Color = Color::Black;
pub const COOKIE: Color = Color::Rgb(167, 139, 250);
pub const STATUS_OK_BG: Color = Color::Rgb(10, 28, 16);
pub const STATUS_WARN_BG: Color = Color::Rgb(28, 22, 8);
pub const STATUS_ERR_BG: Color = Color::Rgb(32, 12, 12);

pub fn fill_bg(f: &mut Frame, area: Rect) {
    f.render_widget(
        Block::default().style(Style::default().bg(BG).fg(FG)),
        area,
    );
}

pub fn style_normal() -> Style {
    Style::default().fg(FG).bg(BG)
}

/// Active tab pill (inverted). List rows do **not** use this — they shimmer.
pub fn style_selected() -> Style {
    Style::default()
        .fg(ON_PILL)
        .bg(ACCENT)
        .add_modifier(Modifier::BOLD)
}

pub fn style_key() -> Style {
    Style::default()
        .fg(ACCENT)
        .bg(BG)
        .add_modifier(Modifier::BOLD)
}

pub fn style_desc() -> Style {
    Style::default().fg(CHROME).bg(BG)
}

pub fn pill(text: impl AsRef<str>, bg: Color) -> Span<'static> {
    Span::styled(
        format!(" {} ", text.as_ref()),
        Style::default()
            .fg(ON_PILL)
            .bg(bg)
            .add_modifier(Modifier::BOLD),
    )
}

/// Focused titles: small accent mark + white/off-white. Unfocused: chrome.
pub fn pane_title(title: &str, focused: bool) -> Line<'static> {
    if focused {
        Line::from(vec![
            Span::styled(" ", Style::default().bg(BG)),
            Span::styled(
                "▸",
                Style::default()
                    .fg(ACCENT)
                    .bg(BG)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!(" {title} "),
                Style::default().fg(FG).bg(BG),
            ),
        ])
    } else {
        Line::from(Span::styled(
            format!(" {title} "),
            Style::default().fg(CHROME).bg(BG),
        ))
    }
}

pub fn bordered(title: &str, focused: bool) -> Block<'static> {
    Block::bordered()
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(if focused { BORDER_FOCUS } else { BORDER }))
        .title(pane_title(title, focused))
        .style(Style::default().bg(BG).fg(FG))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rgb(c: Color) -> (u8, u8, u8) {
        match c {
            Color::Rgb(r, g, b) => (r, g, b),
            other => panic!("expected rgb, got {other:?}"),
        }
    }

    #[test]
    fn palette_is_near_black_canvas() {
        // ANSI Black is the reliable canvas when truecolor is unavailable.
        assert!(
            matches!(BG, Color::Black),
            "BG should be ANSI Black, got {BG:?}"
        );
        assert!(
            matches!(FG, Color::White) || matches!(FG, Color::Rgb(r, g, b) if r >= 220 && g >= 220 && b >= 220),
            "FG should be White or bright off-white, got {FG:?}"
        );
        let (r, g, b) = rgb(BORDER);
        assert!(r < 90 && g < 90 && b < 90, "borders stay dark gray");
        let (r, g, b) = rgb(MUTED);
        assert!(
            r >= 200 && g >= 200 && b >= 200,
            "MUTED must be near-white for readable secondary text, got {MUTED:?}"
        );
        let (r, g, b) = rgb(CHROME);
        assert!(
            r >= 140 && r < 200 && g >= 140 && b >= 140,
            "CHROME is slightly softer than MUTED, got {CHROME:?}"
        );
        assert_ne!(BORDER, ACCENT);
        assert_ne!(BORDER_FOCUS, ACCENT);
        assert_ne!(MUTED, ACCENT);
        assert_ne!(MUTED, INFO);
    }
}
