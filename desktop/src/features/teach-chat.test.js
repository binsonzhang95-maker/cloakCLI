import assert from "node:assert/strict";
import { test } from "node:test";
import { getState } from "../store.js";
import {
  getChatState,
  getThinkingView,
  onTeachEvent,
  resetChatState,
  THINKING_LABEL,
  THINKING_STATUS,
} from "./teach-chat.js";

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

test("job events upsert a teach run for History", async () => {
  resetChatState();
  const { getState } = await import("../store.js");
  onTeachEvent({
    kind: "job",
    job_id: "pending",
    state: "done",
    summary: "click a → done ok",
  });
  const rec = getState().runs.find((r) => r.id === "teach:pending");
  assert.ok(rec, "expected teach run in store");
  assert.equal(rec.state, "done");
  assert.equal(rec.kind, "teach");
});

test("thinking with summary then assistant_delta ends thinking UI", () => {
  resetChatState();
  onTeachEvent({ kind: "job", job_id: "pending", state: "running", summary: "planning" });
  let v = getThinkingView();
  assert.equal(v.active, true);
  assert.equal(v.status, THINKING_STATUS);
  assert.equal(v.text, "");
  onTeachEvent({ kind: "thinking_delta", text: "check the button", seq: 0 });
  onTeachEvent({ kind: "thinking_delta", text: " then click", seq: 1 });
  v = getThinkingView();
  assert.equal(v.active, true);
  assert.equal(v.status, THINKING_LABEL);
  assert.equal(v.text, "check the button then click");
  onTeachEvent({ kind: "thinking_done" });
  v = getThinkingView();
  assert.equal(v.active, false);
  assert.equal(v.done, true);
  assert.equal(v.shimmer, false);
  assert.equal(v.text, "check the button then click");
  onTeachEvent({ kind: "assistant_delta", role: "assistant", text: "Hel", seq: 0, done: false });
  v = getThinkingView();
  assert.equal(v.active, false);
  assert.equal(v.shimmer, false);
  const last = getChatState().messages.at(-1);
  assert.equal(last.role, "assistant");
  assert.equal(last.text, "Hel");
});

test("no provider reasoning shows 正在思考 only — never fabricates a chain", () => {
  resetChatState();
  onTeachEvent({ kind: "job", job_id: "pending", state: "running", summary: "planning" });
  let v = getThinkingView();
  assert.equal(v.active, true);
  assert.equal(v.text, "");
  assert.equal(v.status, THINKING_STATUS);
  onTeachEvent({ kind: "assistant_delta", role: "assistant", text: "Hi", seq: 0, done: false });
  v = getThinkingView();
  assert.equal(v.active, false);
  assert.equal(v.text, "");
  assert.equal(v.show, false);
  assert.equal(getChatState().messages.at(-1).text, "Hi");
});

test("shimmer off keeps static thinking status", () => {
  resetChatState();
  getState().shimmer = false;
  onTeachEvent({ kind: "job", job_id: "pending", state: "running", summary: "planning" });
  onTeachEvent({ kind: "thinking_delta", text: "plan", seq: 0 });
  const v = getThinkingView();
  assert.equal(v.active, true);
  assert.equal(v.shimmer, false);
  getState().shimmer = true;
  const on = getThinkingView();
  assert.equal(on.shimmer, true);
  getState().shimmer = false;
});

test("cancel ends thinking with no leftover active state", () => {
  resetChatState();
  onTeachEvent({ kind: "job", job_id: "pending", state: "running", summary: "planning" });
  onTeachEvent({ kind: "thinking_delta", text: "halfway", seq: 0 });
  assert.equal(getThinkingView().active, true);
  onTeachEvent({
    kind: "job",
    job_id: "pending",
    state: "cancelled",
    summary: "cancelled during planning",
  });
  const v = getThinkingView();
  assert.equal(v.active, false);
  assert.equal(v.done, true);
  assert.equal(v.shimmer, false);
  assert.equal(getChatState().sending, false);
});

test("thinking cross-chunk secret is redacted before display", () => {
  resetChatState();
  onTeachEvent({ kind: "job", job_id: "pending", state: "running", summary: "planning" });
  onTeachEvent({ kind: "thinking_delta", text: "token=abc", seq: 0 });
  let v = getThinkingView();
  assert.equal(v.text.includes("abc123SECRETVALUE"), false, v.text);
  onTeachEvent({ kind: "thinking_delta", text: "123SECRETVALUE more", seq: 1 });
  v = getThinkingView();
  assert.equal(v.text.includes("SECRETVALUE"), false, v.text);
  assert.equal(v.text.includes("abc123"), false, v.text);
  onTeachEvent({ kind: "thinking_done" });
  v = getThinkingView();
  assert.equal(v.text.includes("SECRETVALUE"), false, v.text);
  assert.equal(v.active, false);
});

test("late thinking_delta does not attach after assistant started", () => {
  resetChatState();
  onTeachEvent({ kind: "job", job_id: "pending", state: "running", summary: "planning" });
  onTeachEvent({ kind: "assistant_delta", text: "ok", seq: 0, done: false });
  assert.equal(getThinkingView().active, false);
  onTeachEvent({ kind: "thinking_delta", text: "late chain", seq: 9 });
  assert.equal(getThinkingView().active, false);
  assert.equal(getThinkingView().text.includes("late chain"), false);
  onTeachEvent({ kind: "job", job_id: "pending", state: "done", summary: "no actions" });
  assert.equal(getThinkingView().active, false);
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
