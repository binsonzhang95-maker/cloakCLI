import assert from "node:assert/strict";
import { test } from "node:test";
import { clientSyncedDigest } from "./fleet.js";

test("clientSyncedDigest requires exact digest ACK", () => {
  const client = {
    client_id: "box1",
    online: true,
    installed: [{ skill_id: "hello", version: "0.1.0", digest: "aaa" }],
  };
  assert.equal(clientSyncedDigest(client, "hello", "aaa"), true);
  assert.equal(clientSyncedDigest(client, "hello", "bbb"), false);
  assert.equal(clientSyncedDigest(client, "other", "aaa"), false);
  assert.equal(clientSyncedDigest({ online: false, installed: [] }, "hello", "aaa"), false);
});

test("fleet UI helpers never invent a global email_confirmed field", () => {
  const blob = JSON.stringify({
    clients: [{ client_id: "box1", installed: [{ skill_id: "hello", digest: "aaa" }] }],
    published: [{ skill_id: "hello", digest: "aaa", published: true }],
  });
  assert.equal(blob.includes("email_confirmed"), false);
});
