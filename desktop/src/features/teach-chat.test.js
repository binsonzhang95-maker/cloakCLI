import assert from "node:assert/strict";
import { test } from "node:test";
import { getChatState, onTeachEvent, resetChatState } from "./teach-chat.js";

test("assistant_delta appends incrementally then assistant finalizes", () => {
  resetChatState();
  onTeachEvent({ kind: "assistant_delta", role: "assistant", text: "Hel", seq: 0, done: false });
  onTeachEvent({ kind: "assistant_delta", role: "assistant", text: "lo", seq: 1, done: false });
  let last = getChatState().messages.at(-1);
  assert.equal(last.role, "assistant");
  assert.equal(last.text, "Hello");
  assert.equal(last.streaming, true);
  onTeachEvent({ kind: "assistant", role: "assistant", text: "click a → done ok", done: true });
  last = getChatState().messages.at(-1);
  assert.equal(last.text, "click a → done ok");
  assert.equal(last.streaming, false);
  const assistants = getChatState().messages.filter((m) => m.role === "assistant");
  assert.equal(assistants.length, 1);
});

test("cancel mid-stream stops sending and unmarks streaming", () => {
  resetChatState();
  onTeachEvent({ kind: "job", job_id: "pending", state: "running", summary: "planning" });
  onTeachEvent({ kind: "assistant_delta", text: "partial", seq: 0, done: false });
  assert.equal(getChatState().sending, true);
  onTeachEvent({ kind: "job", job_id: "pending", state: "cancelled", summary: "cancelled during planning" });
  assert.equal(getChatState().sending, false);
  assert.equal(getChatState().messages.at(-1).streaming, false);
  assert.equal(getChatState().job.state, "cancelled");
});

test("resume restores transcript and hub-port note", () => {
  resetChatState();
  onTeachEvent({
    kind: "resume",
    profile: "demo",
    messages: [
      { role: "user", text: "click the link" },
      { role: "assistant", text: "click a → done ok" },
    ],
    tools: [{ summary: "click a", status: "ok" }],
    phase: "chat",
    status: "restored",
    hub_resume: "new_hub",
    note: "Teach hub binds a new ephemeral port each spawn",
  });
  const c = getChatState();
  assert.equal(c.messages[0].role, "user");
  assert.equal(c.messages[0].text, "click the link");
  assert.equal(c.messages[1].role, "assistant");
  assert.ok(c.messages.some((m) => String(m.text).includes("ephemeral port")));
  assert.equal(c.tools[0].status, "ok");
  assert.equal(c.status.hub_resume, "new_hub");
});

test("closed keeps transcript so reconnect can continue", () => {
  resetChatState();
  onTeachEvent({ kind: "user", role: "user", text: "click the link" });
  onTeachEvent({ kind: "closed", reason: "child_exit", profile: "demo" });
  const c = getChatState();
  assert.equal(c.session, null);
  assert.ok(c.messages.some((m) => m.role === "user" && m.text === "click the link"));
  assert.ok(c.messages.some((m) => String(m.text).includes("session closed")));
  onTeachEvent({ kind: "closed", reason: "child_exit", profile: "demo" });
  const closedNotes = c.messages.filter((m) => String(m.text).startsWith("session closed"));
  assert.equal(closedNotes.length, 1);
});
