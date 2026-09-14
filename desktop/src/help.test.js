import assert from "node:assert/strict";
import { test } from "node:test";
import { SHORTCUTS, renderShortcutsTable, shortcutsStatusHint } from "./help.js";

test("shortcut list covers nav, inspector, settings, help", () => {
  const keys = SHORTCUTS.map((s) => s.key);
  for (const k of ["1", "2", "3", "4", "5", "[", ",", "?", "Esc", "Enter"]) {
    assert.equal(keys.includes(k), true, `missing ${k}`);
  }
  const blob = SHORTCUTS.map((s) => s.action).join(" ");
  assert.match(blob, /Teach Chat/);
  assert.match(blob, /Runs/);
  assert.match(blob, /Diagnostics/);
  assert.match(blob, /inspector/i);
  assert.match(blob, /Settings/);
});

test("status hint and overlay table stay in sync", () => {
  const hint = shortcutsStatusHint();
  assert.match(hint, /1–5/);
  assert.match(hint, /\? help/);
  const table = renderShortcutsTable();
  assert.match(table, /<kbd>4<\/kbd>/);
  assert.match(table, /Runs \/ History/);
});
