export const SHORTCUTS = [
  { key: "1", action: "Fleet (ops home)" },
  { key: "2", action: "Teach Chat" },
  { key: "3", action: "Skills" },
  { key: "4", action: "Profiles" },
  { key: "5", action: "Runs (history / ledger)" },
  { key: "6", action: "Diagnostics (Raw TUI)" },
  { key: "[", action: "Toggle inspector" },
  { key: ",", action: "Settings" },
  { key: "?", action: "Keyboard shortcuts overlay" },
  { key: "Esc", action: "Close overlay / settings" },
  { key: "Enter", action: "Send chat (Shift+Enter newline)" },
];

export function shortcutsStatusHint() {
  return "1–6 nav · [ inspector · , settings · ? help";
}

export function renderShortcutsTable() {
  return SHORTCUTS.map(
    (s) =>
      `<div class="help-row"><kbd>${s.key}</kbd><span>${s.action}</span></div>`,
  ).join("");
}
