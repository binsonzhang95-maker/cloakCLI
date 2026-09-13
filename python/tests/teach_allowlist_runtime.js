#!/usr/bin/env node
/**
 * Runtime allowlist test for extensions/teach/background.js.
 * Proves an unapproved second origin is not injected or recorded.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const bgPath = path.resolve(__dirname, "../../extensions/teach/background.js");
const src = fs.readFileSync(bgPath, "utf8");

const injected = [];
const permissionRequests = [];
let tabs = [{ id: 1, url: "https://example.com/login", active: true }];
const messageListeners = [];
const committedListeners = [];
const updatedListeners = [];
const fetchCalls = [];

const sessionCfg = {
  exportOrigin: "http://127.0.0.1:9",
  token: "test-token",
  allowOrigins: ["https://example.com"],
  allowSecrets: false,
  profile: "demo",
  hubUrl: "ws://127.0.0.1:9",
  pairingCode: "TEST01",
  pairingId: "pair-test",
};

const sessionStore = {};
const tabMessages = [];
const hubSent = [];

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
  }
  send() {}
  close() {
    this.readyState = 3;
    if (typeof this.onclose === "function") this.onclose();
  }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;

const chrome = {
  runtime: {
    getURL: (p) => "chrome-extension://teach/" + p,
    onInstalled: { addListener: () => {} },
    onMessage: {
      addListener: (fn) => messageListeners.push(fn),
    },
    lastError: null,
  },
  webNavigation: {
    onCommitted: {
      addListener: (fn) => committedListeners.push(fn),
    },
  },
  tabs: {
    onUpdated: {
      addListener: (fn) => updatedListeners.push(fn),
    },
    query: async (q = {}) => {
      if (q && q.active) return tabs.filter((t) => t.active);
      return tabs.slice();
    },
    sendMessage: async (tabId, msg) => {
      tabMessages.push({ tabId, msg });
      return null;
    },
  },
  scripting: {
    executeScript: async (opts) => {
      injected.push({
        tabId: opts.target && opts.target.tabId,
        files: opts.files,
      });
      return [];
    },
  },
  permissions: {
    request: async (p) => {
      permissionRequests.push(p);
      return true;
    },
  },
  storage: {
    session: {
      get: async (keys) => {
        const out = {};
        for (const k of keys) if (sessionStore[k] !== undefined) out[k] = sessionStore[k];
        return out;
      },
      set: async (obj) => {
        Object.assign(sessionStore, obj);
      },
    },
  },
};

async function fakeFetch(url) {
  fetchCalls.push(String(url));
  if (String(url).includes("session.json")) {
    return { ok: true, json: async () => sessionCfg };
  }
  throw new Error("unexpected fetch " + url);
}

const sandbox = {
  chrome,
  fetch: fakeFetch,
  console,
  URL,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  Promise,
  Map,
  Set,
  JSON,
  Object,
  Array,
  String,
  Boolean,
  Number,
  Error,
  TypeError,
  WebSocket: FakeWebSocket,
  crypto,
  self: null,
  importScripts: (name) => {
    if (String(name) !== "pairing.js") {
      throw new Error("unexpected importScripts " + name);
    }
    const pairingPath = path.resolve(__dirname, "../../extensions/teach/pairing.js");
    vm.runInContext(fs.readFileSync(pairingPath, "utf8"), sandbox);
  },
};
sandbox.self = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
if (sandbox.TeachHub && sandbox.TeachHub.send) {
  sandbox.TeachHub.send = function (type, data) {
    hubSent.push({ type, data });
    return true;
  };
  sandbox.TeachHub.paired = true;
}

function fail(msg) {
  console.error("FAIL:", msg);
  process.exit(1);
}

function assert(cond, msg) {
  if (!cond) fail(msg);
}

function send(msg, sender = {}) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(
      () => reject(new Error("timeout waiting for " + JSON.stringify(msg))),
      2000
    );
    const wrapped = (r) => {
      clearTimeout(t);
      resolve(r);
    };
    assert(messageListeners.length === 1, "expected one onMessage listener");
    const keep = messageListeners[0](msg, sender, wrapped);
    assert(keep === true, "onMessage should return true for async sendResponse");
  });
}

async function commit(url, tabId) {
  const tab = tabs.find((t) => t.id === tabId) || tabs[0];
  if (tab && tabId === tab.id) tab.url = url;
  for (const fn of committedListeners) {
    await fn({ frameId: 0, tabId, url });
  }
}

async function complete(tabId, url) {
  const tab = tabs.find((t) => t.id === tabId) || { id: tabId, url };
  tab.url = url;
  for (const fn of updatedListeners) {
    await fn(tabId, { status: "complete" }, tab);
  }
}

(async () => {
  const st0 = await send({ type: "status" });
  assert(st0.ok, "status after loadConfig");
  assert(st0.hasSession, "session.json loaded");
  assert(
    JSON.stringify(st0.allowlist) === JSON.stringify(["https://example.com"]),
    "CLI --url origin is the only seed allowlist, got " + JSON.stringify(st0.allowlist)
  );

  const started = await send({ type: "start" });
  assert(started.ok && started.recording, "start recording");
  assert(started.n === 1, "start records navigation on allowlisted tab, n=" + started.n);
  assert(
    injected.some((i) => i.tabId === 1 && i.files && i.files.includes("content.js")),
    "allowlisted origin injected on start"
  );
  const injectAfterStart = injected.length;
  const permAfterStart = permissionRequests.length;
  assert(permAfterStart === 0, "start must not call permissions.request / silent-add");

  await commit("https://example.com/app", 1);
  const st1 = await send({ type: "status" });
  assert(st1.n === 2, "same-origin navigation recorded, n=" + st1.n);
  assert(injected.length > injectAfterStart, "same-origin navigation injected");

  const nBeforeEvil = st1.n;
  const injectBeforeEvil = injected.length;
  const permBeforeEvil = permissionRequests.length;

  tabs.push({ id: 2, url: "https://evil.example/phish", active: false });
  await commit("https://evil.example/phish", 2);
  await complete(2, "https://evil.example/phish");

  const st2 = await send({ type: "status" });
  assert(
    st2.n === nBeforeEvil,
    "unapproved second origin must not be recorded, n=" + st2.n + " want " + nBeforeEvil
  );
  assert(
    injected.length === injectBeforeEvil,
    "unapproved second origin must not be injected, injects=" +
      injected.length +
      " want " +
      injectBeforeEvil
  );
  assert(
    !st2.allowlist.includes("https://evil.example"),
    "unapproved origin must not join allowlist: " + JSON.stringify(st2.allowlist)
  );
  assert(
    permissionRequests.length === permBeforeEvil,
    "navigation must not call permissions.request, calls=" + permissionRequests.length
  );
  assert(
    st2.ignoredOrigin === "https://evil.example",
    "ignoredOrigin should surface the blocked site, got " + st2.ignoredOrigin
  );

  const hubBeforeEvil = hubSent.length;
  const psEvil = await send(
    {
      type: "page_state",
      state: {
        url: "https://evil.example/phish",
        origin: "https://evil.example",
        title: "phish",
        viewport: { width: 1, height: 1 },
        observation_id: "obs-evil",
      },
    },
    { url: "https://evil.example/phish", tab: { id: 2, url: "https://evil.example/phish" } }
  );
  assert(psEvil.ok === false, "page_state from unapproved origin must fail");
  assert(
    hubSent.length === hubBeforeEvil,
    "unapproved origin must not send page_state to hub, n=" + hubSent.length
  );

  const rec = await send(
    { type: "record", event: { kind: "click", selector: "#x" } },
    { url: "https://evil.example/phish", tab: { id: 2, url: "https://evil.example/phish" } }
  );
  assert(rec.ok === false, "content-script record from unapproved origin must fail");
  assert(
    String(rec.error || "").includes("allowlist"),
    "record error should mention allowlist, got " + rec.error
  );
  const st3 = await send({ type: "status" });
  assert(st3.n === nBeforeEvil, "rejected record must not append events");

  const fromContent = await send(
    { type: "approveOrigin", origin: "https://evil.example" },
    { tab: { id: 2, url: "https://evil.example/phish" }, url: "https://evil.example/phish" }
  );
  assert(fromContent.ok === false, "content script must not approve origins");
  assert(
    !(await send({ type: "status" })).allowlist.includes("https://evil.example"),
    "rejected content-script approve must not add origin"
  );

  const approved = await send(
    { type: "approveOrigin", origin: "https://evil.example" },
    { url: "chrome-extension://teach/popup.html" }
  );
  assert(approved.ok, "popup approveOrigin should succeed: " + JSON.stringify(approved));
  assert(
    approved.allowlist.includes("https://evil.example"),
    "explicit approve adds second origin"
  );
  assert(
    permissionRequests.some(
      (p) => p.origins && p.origins.includes("https://evil.example/*")
    ),
    "explicit approve is the only permissions.request for evil.example"
  );

  const nAfterApprove = (await send({ type: "status" })).n;
  const injectAfterApprove = injected.length;
  await commit("https://evil.example/next", 2);
  const st4 = await send({ type: "status" });
  assert(st4.n > nAfterApprove, "approved second origin navigation is recorded");
  assert(injected.length > injectAfterApprove, "approved second origin is injected");

  const psOk = await send(
    {
      type: "page_state",
      state: {
        url: "https://example.com/app",
        origin: "https://example.com",
        title: "Dashboard",
        viewport: { width: 1280, height: 720 },
        observation_id: "obs-ok",
      },
    },
    { url: "https://example.com/app", tab: { id: 1, url: "https://example.com/app" } }
  );
  assert(psOk.ok === true, "allowlisted page_state should forward: " + JSON.stringify(psOk));
  assert(
    hubSent.some((h) => h.type === "page_state" && h.data && h.data.observation_id === "obs-ok"),
    "hub should receive allowlisted page_state"
  );

  console.log("PASS: CLI origin injected+recorded");
  console.log("PASS: unapproved second origin not injected");
  console.log("PASS: unapproved second origin not recorded");
  console.log("PASS: unapproved origin not added to allowlist");
  console.log("PASS: navigation does not call permissions.request");
  console.log("PASS: explicit popup approve then injects+records");
  console.log("PASS: unapproved origin does not send page_state");
  console.log("PASS: allowlisted origin forwards page_state");
  process.exit(0);
})().catch((e) => {
  console.error("FAIL:", e && e.stack ? e.stack : e);
  process.exit(1);
});
