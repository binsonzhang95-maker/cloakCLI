import assert from "node:assert/strict";
import { test } from "node:test";
import { redactText } from "../redact.js";
import { runFromJobEvent } from "./runs.js";

test("runFromJobEvent maps a teach job without dropping state", () => {
  const run = runFromJobEvent(
    { kind: "job", job_id: "pending", state: "done", summary: "click a → done ok" },
    "demo",
  );
  assert.equal(run.id, "teach:pending");
  assert.equal(run.kind, "teach");
  assert.equal(run.profile, "demo");
  assert.equal(run.state, "done");
  assert.equal(run.summary, "click a → done ok");
});

test("history summaries are redacted before display", () => {
  const run = runFromJobEvent({
    job_id: "x",
    state: "failed",
    summary: "Authorization: Bearer sk-secretTEST99abc",
    error: "cookie=SESSIONID_SUPER_SECRET",
  });
  const summary = redactText(run.summary);
  const error = redactText(run.error);
  assert.equal(summary.includes("sk-secretTEST99abc"), false, summary);
  assert.equal(error.includes("SESSIONID_SUPER_SECRET"), false, error);
});
