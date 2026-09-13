/* CloakCLI Teach Hub client (M1): pairing, reconnect, page_state send.
 * Loaded by background.js via importScripts. Loopback WebSocket only.
 */
(function (root) {
  const MAX_BACKOFF_MS = 15000;
  const HEARTBEAT_MS = 15000;

  const TeachHub = {
    cfg: null,
    ws: null,
    sessionId: null,
    sessionToken: null,
    paired: false,
    closed: false,
    backoffMs: 250,
    heartbeatTimer: null,
    reconnectTimer: null,
    lastStatus: "idle",
  };

  function nonce() {
    const a = new Uint8Array(16);
    const c = root.crypto || (typeof crypto !== "undefined" ? crypto : null);
    if (c && c.getRandomValues) c.getRandomValues(a);
    else for (let i = 0; i < a.length; i++) a[i] = (i * 17 + 31) & 0xff;
    return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function envelope(type, data) {
    const env = {
      v: 1,
      type,
      ts: new Date().toISOString(),
      data: data || {},
    };
    if (TeachHub.sessionId) env.session_id = TeachHub.sessionId;
    return env;
  }

  async function loadStored() {
    try {
      if (!root.chrome || !chrome.storage || !chrome.storage.session) return;
      const got = await chrome.storage.session.get([
        "teachSessionId",
        "teachSessionToken",
      ]);
      if (got && got.teachSessionId && got.teachSessionToken) {
        TeachHub.sessionId = got.teachSessionId;
        TeachHub.sessionToken = got.teachSessionToken;
      }
    } catch {
      /* storage.session may be unavailable in tests */
    }
  }

  async function storeSession(id, token) {
    TeachHub.sessionId = id;
    TeachHub.sessionToken = token;
    try {
      if (root.chrome && chrome.storage && chrome.storage.session) {
        await chrome.storage.session.set({
          teachSessionId: id,
          teachSessionToken: token,
        });
      }
    } catch {
      /* memory-only fallback */
    }
  }

  function setStatus(s) {
    TeachHub.lastStatus = s;
  }

  function sendRaw(env) {
    if (!TeachHub.ws || TeachHub.ws.readyState !== 1) return false;
    try {
      TeachHub.ws.send(JSON.stringify(env));
      return true;
    } catch {
      return false;
    }
  }

  function sendPairingAccept() {
    const data = {
      nonce: nonce(),
      role: "extension",
    };
    if (TeachHub.sessionToken) {
      data.session_token = TeachHub.sessionToken;
      data.resume_from = 0;
    } else {
      data.pairing_id = TeachHub.cfg && TeachHub.cfg.pairingId;
      data.code = TeachHub.cfg && TeachHub.cfg.pairingCode;
    }
    sendRaw(envelope("pairing_accept", data));
  }

  function onMessage(ev) {
    let env;
    try {
      env = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (!env || typeof env !== "object" || env.v !== 1) return;
    const type = env.type;
    const data = env.data || {};
    if (type === "pairing_offer") {
      if (!TeachHub.paired) sendPairingAccept();
      return;
    }
    if (type === "pairing_result") {
      if (data.ok && data.session_id && data.session_token) {
        storeSession(data.session_id, data.session_token);
        TeachHub.paired = true;
        TeachHub.backoffMs = 250;
        setStatus(data.resumed ? "reconnected" : "paired");
        startHeartbeat();
      } else {
        TeachHub.paired = false;
        setStatus("pairing_failed");
      }
      return;
    }
    if (type === "error") {
      if (data && data.code === "unauthorized") {
        TeachHub.paired = false;
      }
    }
  }

  function startHeartbeat() {
    if (TeachHub.heartbeatTimer) {
      clearInterval(TeachHub.heartbeatTimer);
    }
    TeachHub.heartbeatTimer = setInterval(() => {
      if (TeachHub.paired) sendRaw(envelope("heartbeat", { ok: true }));
    }, HEARTBEAT_MS);
  }

  function scheduleReconnect() {
    if (TeachHub.closed) return;
    if (TeachHub.reconnectTimer) return;
    const wait = TeachHub.backoffMs;
    TeachHub.backoffMs = Math.min(TeachHub.backoffMs * 2, MAX_BACKOFF_MS);
    TeachHub.reconnectTimer = setTimeout(() => {
      TeachHub.reconnectTimer = null;
      connect();
    }, wait);
  }

  function connect() {
    if (TeachHub.closed) return;
    const url = TeachHub.cfg && TeachHub.cfg.hubUrl;
    if (!url || !String(url).startsWith("ws://127.0.0.1")) {
      setStatus("no_hub");
      return;
    }
    try {
      const ws = new WebSocket(url);
      TeachHub.ws = ws;
      setStatus("connecting");
      ws.onopen = () => {
        setStatus("connected");
        sendPairingAccept();
      };
      ws.onmessage = onMessage;
      ws.onclose = () => {
        TeachHub.paired = false;
        setStatus("disconnected");
        if (TeachHub.heartbeatTimer) {
          clearInterval(TeachHub.heartbeatTimer);
          TeachHub.heartbeatTimer = null;
        }
        scheduleReconnect();
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
    } catch {
      scheduleReconnect();
    }
  }

  TeachHub.start = async function start(cfg) {
    if (!cfg || !cfg.hubUrl) return;
    TeachHub.cfg = cfg;
    TeachHub.closed = false;
    TeachHub.backoffMs = 250;
    await loadStored();
    connect();
  };

  TeachHub.stop = function stop() {
    TeachHub.closed = true;
    if (TeachHub.reconnectTimer) {
      clearTimeout(TeachHub.reconnectTimer);
      TeachHub.reconnectTimer = null;
    }
    if (TeachHub.heartbeatTimer) {
      clearInterval(TeachHub.heartbeatTimer);
      TeachHub.heartbeatTimer = null;
    }
    try {
      if (TeachHub.ws) TeachHub.ws.close();
    } catch {
      /* ignore */
    }
  };

  TeachHub.send = function send(type, data) {
    if (!TeachHub.paired) return false;
    return sendRaw(envelope(type, data));
  };

  TeachHub.status = function status() {
    return {
      hub: TeachHub.lastStatus,
      paired: TeachHub.paired,
      sessionId: TeachHub.sessionId || "",
    };
  };

  root.TeachHub = TeachHub;
})(typeof self !== "undefined" ? self : this);
