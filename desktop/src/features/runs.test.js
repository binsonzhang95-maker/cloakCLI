import assert from "node:assert/strict";
import { test } from "node:test";
import { redactText } from "../redact.js";
import { partitionRunsBySkill, runFromJobEvent } from "./runs.js";

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

test("fleet job result is partitioned by skill and has no global email_confirmed", () => {
  const pin = runFromJobEvent({
    kind: "fleet",
    job_id: "j1",
    skill: "pin-reg",
    state: "succeeded",
    result: { skill_id: "pin-reg", status: "logged_in", label: "已登录", success: true, digest: "aaa" },
    source: "fleet",
  });
  const ship = runFromJobEvent({
    kind: "fleet",
    job_id: "j2",
    skill: "ship-demo",
    state: "succeeded",
    result: { skill_id: "ship-demo", status: "shipped", label: "Shipped", success: true, digest: "bbb" },
    source: "fleet",
  });
  assert.equal(pin.status, "logged_in");
  assert.equal(ship.status, "shipped");
  assert.equal("email_confirmed" in pin, false);
  const parts = partitionRunsBySkill([pin, ship]);
  assert.deepEqual(Object.keys(parts).sort(), ["pin-reg", "ship-demo"]);
  assert.equal(parts["pin-reg"][0].label, "已登录");
  assert.equal(JSON.stringify(parts).includes("email_confirmed"), false);
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
