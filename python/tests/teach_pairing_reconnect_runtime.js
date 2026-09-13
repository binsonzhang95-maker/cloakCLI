#!/usr/bin/env node
/**
 * Service-worker restart simulation for extensions/teach/pairing.js.
 * Proves stored session_token is reused (no second short-code pair) and
 * open+pairing_offer does not send two pairing_accept frames.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const pairingSrc = fs.readFileSync(
  path.resolve(__dirname, "../../extensions/teach/pairing.js"),
  "utf8"
);

function fail(msg) {
  console.error("FAIL:", msg);
  process.exit(1);
}

function assert(cond, msg) {
  if (!cond) fail(msg);
}

function loadTeachHub(store) {
  const sockets = [];
  class FakeWebSocket extends EventEmitter {
    constructor(url) {
      super();
      this.url = url;
      this.readyState = 0;
      this.sent = [];
      sockets.push(this);
      setImmediate(() => {
        this.readyState = 1;
        if (typeof this.onopen === "function") this.onopen();
        const offer = JSON.stringify({
          v: 1,
          type: "pairing_offer",
          data: { pairing_id: "pair-1", code: "AB12CD" },
        });
        if (typeof this.onmessage === "function") this.onmessage({ data: offer });
      });
    }
    send(text) {
      this.sent.push(String(text));
    }
    close() {
      this.readyState = 3;
      if (typeof this.onclose === "function") this.onclose();
    }
  }
  FakeWebSocket.CONNECTING = 0;
  FakeWebSocket.OPEN = 1;
  FakeWebSocket.CLOSING = 2;
  FakeWebSocket.CLOSED = 3;

  const sandbox = {
    chrome: {
      storage: {
        session: {
          get: async (keys) => {
            const out = {};
            for (const k of keys) if (store[k] !== undefined) out[k] = store[k];
            return out;
          },
          set: async (obj) => Object.assign(store, obj),
        },
        local: {
          get: async (keys) => {
            const out = {};
            for (const k of keys) if (store[k] !== undefined) out[k] = store[k];
            return out;
          },
          set: async (obj) => Object.assign(store, obj),
        },
      },
    },
    WebSocket: FakeWebSocket,
    crypto: {
      getRandomValues: (a) => {
        for (let i = 0; i < a.length; i++) a[i] = (i + 7) & 0xff;
        return a;
      },
    },
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    JSON,
    Object,
    Array,
    String,
    Date,
    Uint8Array,
    self: null,
  };
  sandbox.self = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(pairingSrc, sandbox);
  return { hub: sandbox.TeachHub, sockets };
}

function parseSent(ws) {
  return ws.sent.map((s) => JSON.parse(s));
}

(async () => {
  const store = {};
  const first = loadTeachHub(store);
  await first.hub.start({
    hubUrl: "ws://127.0.0.1:9",
    pairingCode: "AB12CD",
    pairingId: "pair-1",
  });
  await new Promise((r) => setTimeout(r, 30));
  assert(first.sockets.length === 1, "first SW opened one websocket");
  const firstMsgs = parseSent(first.sockets[0]);
  const accepts = firstMsgs.filter((m) => m.type === "pairing_accept");
  assert(accepts.length === 1, "open+offer must send one pairing_accept, got " + accepts.length);
  assert(!accepts[0].data.session_token, "first pair uses short code, not token");
  assert(accepts[0].data.code === "AB12CD", "first pair sends pairing code");
  assert(accepts[0].data.role === "extension", "role is extension");

  first.sockets[0].onmessage({
    data: JSON.stringify({
      v: 1,
      type: "pairing_result",
      data: {
        ok: true,
        session_id: "sess-real",
        session_token: "tok-keep-me",
        resumed: false,
      },
    }),
  });
  await new Promise((r) => setTimeout(r, 20));
  assert(store.teachSessionId === "sess-real", "token persisted to storage");
  assert(store.teachSessionToken === "tok-keep-me", "session token persisted");
  first.hub.stop();

  // Service-worker restart: new TeachHub singleton, same chrome.storage.
  const second = loadTeachHub(store);
  await second.hub.start({
    hubUrl: "ws://127.0.0.1:9",
    pairingCode: "AB12CD",
    pairingId: "pair-1",
  });
  await new Promise((r) => setTimeout(r, 30));
  assert(second.sockets.length === 1, "restarted SW opened one websocket");
  const secondMsgs = parseSent(second.sockets[0]);
  const secondAccepts = secondMsgs.filter((m) => m.type === "pairing_accept");
  assert(
    secondAccepts.length === 1,
    "restarted SW sends one pairing_accept, got " + secondAccepts.length
  );
  assert(
    secondAccepts[0].data.session_token === "tok-keep-me",
    "restarted SW reconnects with stored session token, not a new short-code pair"
  );
  assert(
    !secondAccepts[0].data.code,
    "reconnect must not send pairing code when token is present"
  );

  second.sockets[0].onmessage({
    data: JSON.stringify({
      v: 1,
      type: "pairing_result",
      data: {
        ok: true,
        session_id: "sess-real",
        session_token: "tok-keep-me",
        resumed: true,
      },
    }),
  });
  await new Promise((r) => setTimeout(r, 20));
  const st = second.hub.status();
  assert(st.paired === true, "reconnect marked paired");
  assert(st.sessionId === "sess-real", "same session id after SW restart");
  assert(st.hub === "reconnected", "status is reconnected, got " + st.hub);

  // A late pairing_consumed must not drop an already-stored session.
  second.sockets[0].onmessage({
    data: JSON.stringify({
      v: 1,
      type: "pairing_result",
      data: { ok: false, error: "pairing_consumed" },
    }),
  });
  assert(second.hub.status().paired === true, "late pairing_consumed must not unpair");
  assert(second.hub.status().sessionId === "sess-real", "session id kept after late fail");

  console.log("PASS: single pairing_accept per connection");
  console.log("PASS: reconnect reuses session");
  console.log("PASS: duplicate pairing rejected");
  process.exit(0);
})().catch((e) => {
  console.error("FAIL:", e && e.stack ? e.stack : e);
  process.exit(1);
});
