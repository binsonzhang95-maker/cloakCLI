import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createStreamRedactor,
  looksLikeSecret,
  mockAckPreview,
  redactEventPayload,
  redactText,
  safeRedactedDisplay,
  summarizeUserMessage,
} from "./redact.js";

const SAMPLES = [
  {
    name: "Authorization bearer",
    raw: "Authorization: Bearer sk-secretTEST99abc",
    leak: "sk-secretTEST99abc",
  },
  {
    name: "cookie assignment",
    raw: "please store cookie=SESSIONID_SUPER_SECRET for me",
    leak: "SESSIONID_SUPER_SECRET",
  },
  {
    name: "token assignment",
    raw: "token=abc123SECRETVALUE",
    leak: "abc123SECRETVALUE",
  },
  {
    name: "json token",
    raw: '{"token":"super-secret-token"}',
    leak: "super-secret-token",
  },
];

test("looksLikeSecret flags token / cookie / Authorization", () => {
  for (const { raw } of SAMPLES) {
    assert.equal(looksLikeSecret(raw), true, raw);
  }
  assert.equal(looksLikeSecret("Describe a login flow on example.com"), false);
});

test("summarizeUserMessage omits pasted secrets instead of echoing them", () => {
  for (const { raw, leak } of SAMPLES) {
    const s = summarizeUserMessage(raw);
    assert.equal(s.hadSecrets, true, raw);
    assert.equal(s.display.includes("secrets omitted"), true, s.display);
    assert.equal(s.display.includes(leak), false, s.display);
    assert.equal(s.display.includes(raw.slice(0, 80)), false, s.display);
    const ack = mockAckPreview(s);
    assert.equal(ack.includes("preview: [omitted]"), true, ack);
    assert.equal(ack.includes(leak), false, ack);
    assert.equal(ack.includes(raw.slice(0, 80)), false, ack);
  }
});

test("plain teach messages are stored, not sliced into the ack", () => {
  const raw = "Open example.com and click Sign in";
  const s = summarizeUserMessage(raw);
  assert.equal(s.hadSecrets, false);
  assert.equal(s.display, raw);
  const ack = mockAckPreview(s);
  assert.equal(ack.includes(raw), false);
  assert.equal(ack.includes("preview: [omitted]"), true);
  assert.equal(ack.includes("secrets: none"), true);
});

test("redactEventPayload redacts free-text and keeps pairing ids", () => {
  const ev = redactEventPayload({
    kind: "assistant",
    pairing_code: "K7Q2MX",
    session_id: "sess-1",
    text: "Authorization: Bearer sk-secretTEST99abc cookie=SESSIONID_SUPER_SECRET",
    summary: "token=abc123SECRETVALUE",
  });
  assert.equal(ev.kind, "assistant");
  assert.equal(ev.pairing_code, "K7Q2MX");
  assert.equal(ev.session_id, "sess-1");
  assert.equal(ev.text.includes("sk-secretTEST99abc"), false, ev.text);
  assert.equal(ev.text.includes("SESSIONID_SUPER_SECRET"), false, ev.text);
  assert.equal(ev.summary.includes("abc123SECRETVALUE"), false, ev.summary);
});

test("safeRedactedDisplay holds incomplete prefix then redacts across chunks", () => {
  const r = createStreamRedactor();
  const a = r.push("token=abc");
  assert.equal(a.includes("abc123SECRETVALUE"), false, a);
  const b = r.push("123SECRETVALUE more");
  assert.equal(b.includes("SECRETVALUE"), false, b);
  assert.equal(b.includes("abc123"), false, b);
  const c = r.flush();
  assert.equal(c.includes("SECRETVALUE"), false, c);
  assert.equal(c.includes("***"), true, c);
});

test("safeRedactedDisplay does not flash sk- prefix until complete", () => {
  assert.equal(safeRedactedDisplay("sk-", false), "");
  const shown = safeRedactedDisplay("sk-secretTEST99abc", false);
  assert.equal(shown.includes("secretTEST99abc"), false, shown);
  assert.equal(shown.includes("sk-***") || shown.includes("***"), true, shown);
});

test("redactText strips remaining secret values", () => {
  const s = redactText(
    'Authorization: Bearer sk-secretTEST99abc cookie=abc123 {"token":"xyzTOK"} http://user:hunter2@127.0.0.1:7890',
  );
  assert.equal(s.includes("sk-secretTEST99abc"), false, s);
  assert.equal(s.includes("abc123"), false, s);
  assert.equal(s.includes("xyzTOK"), false, s);
  assert.equal(s.includes("hunter2"), false, s);
  assert.equal(s.includes("***"), true, s);
});
