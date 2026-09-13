/* CloakCLI Teach — service worker.
 * Origin allowlist: CLI --url origin from session.json, plus origins the
 * user explicitly Allows in the popup. Navigating does not silent-add.
 * Allowlist still gates content-script injection and page_state.
 * Product rule: any http(s) navigation (goto) is recorded even when the
 * origin is not allowlisted, so takeover → Playwright can emit goto.
 * javascript:/file:/data: and other non-http schemes are rejected.
 */
importScripts("pairing.js");

const state = {
  cfg: null,
  recording: false,
  takeover: false,
  events: [],
  goal: "",
  allowlist: new Set(),
  ignoredOrigin: "",
  assistInFlight: false,
};

let configLoad = null;

function isHttpUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function isHttpOrigin(origin) {
  return isHttpUrl(origin);
}

function isNavigationKind(kind) {
  const k = String(kind || "").toLowerCase();
  return k === "navigation" || k === "nav" || k === "goto";
}

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

function exportOrigin() {
  return (state.cfg && state.cfg.exportOrigin) || "";
}

function originAllowed(origin) {
  if (!origin || !isHttpOrigin(origin)) return false;
  if (exportOrigin() && origin === originOf(exportOrigin())) return false;
  return state.allowlist.has(origin);
}

async function loadConfig() {
  if (state.cfg) return state.cfg;
  if (configLoad) return configLoad;
  configLoad = (async () => {
    try {
      const r = await fetch(chrome.runtime.getURL("session.json"));
      if (!r.ok) {
        configLoad = null;
        return null;
      }
      state.cfg = await r.json();
      for (const o of state.cfg.allowOrigins || []) {
        if (isHttpOrigin(o) && !(exportOrigin() && o === originOf(exportOrigin()))) {
          state.allowlist.add(o);
        }
      }
      startHub();
      return state.cfg;
    } catch {
      configLoad = null;
      return null;
    }
  })();
  return configLoad;
}

function startHub() {
  if (!state.cfg || !state.cfg.hubUrl) return;
  if (typeof TeachHub === "undefined" || !TeachHub.start) return;
  TeachHub.onHubMessage = handleHubMessage;
  TeachHub.start({
    hubUrl: state.cfg.hubUrl,
    pairingCode: state.cfg.pairingCode,
    pairingId: state.cfg.pairingId,
  });
}

function hubSend(type, data) {
  if (typeof TeachHub === "undefined" || !TeachHub.send) return false;
  return TeachHub.send(type, data);
}

function hubStatus() {
  if (typeof TeachHub === "undefined" || !TeachHub.status) {
    return { hub: "idle", paired: false, sessionId: "" };
  }
  return TeachHub.status();
}

function forwardPageState(snapshot, senderUrl) {
  if (!snapshot || typeof snapshot !== "object") return false;
  const origin = snapshot.origin || originOf(snapshot.url || senderUrl || "");
  if (!originAllowed(origin)) return false;
  return hubSend("page_state", snapshot);
}

async function requestPageState(tabId, url) {
  const origin = originOf(url || "");
  if (!originAllowed(origin)) return;
  try {
    const snap = await chrome.tabs.sendMessage(tabId, { type: "collectPageState" });
    if (snap) forwardPageState(snap, url);
  } catch {
    /* content script may not be ready */
  }
}

function addOrigin(origin) {
  if (!isHttpOrigin(origin)) return false;
  if (exportOrigin() && origin === originOf(exportOrigin())) return false;
  state.allowlist.add(origin);
  if (state.ignoredOrigin === origin) state.ignoredOrigin = "";
  return true;
}

async function requestOrigin(origin) {
  if (!addOrigin(origin)) return false;
  try {
    await chrome.permissions.request({ origins: [`${origin}/*`] });
  } catch {
    /* optional_host_permissions may already be granted for unpacked --load-extension */
  }
  return true;
}

async function inject(tabId, url) {
  const origin = originOf(url || "");
  if (!originAllowed(origin)) return false;
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      files: ["content.js"],
    });
    return true;
  } catch {
    /* tab may be chrome:// or gone */
    return false;
  }
}

function redactTakeoverEvent(event) {
  if (!event || typeof event !== "object") return event;
  const out = { ...event };
  const field = out.field || {};
  const hay = [field.type, field.name, field.id, field.autocomplete, out.label, out.kind]
    .join(" ")
    .toLowerCase();
  const secret =
    out.redacted ||
    field.type === "password" ||
    /password|passwd|secret|token|authorization|cookie|credential/.test(hay);
  if (secret) {
    if (typeof out.value === "string") {
      out.value_len = out.value_len || out.value.length;
    }
    out.value = "";
    out.redacted = true;
    if (out.text && /password|token|secret/.test(String(out.text).toLowerCase())) {
      out.text = "[REDACTED]";
    }
  }
  return out;
}

function record(event) {
  if (!state.recording && !state.takeover) return;
  if (!event || typeof event !== "object") return;
  const kind = String(event.kind || "").toLowerCase();
  if (isNavigationKind(kind)) {
    const url = String(event.url || "");
    // Any http(s) goto is recorded even off-allowlist. Reject other schemes.
    if (!isHttpUrl(url)) return;
  }
  const stored = state.takeover ? redactTakeoverEvent(event) : event;
  if (state.recording) state.events.push(stored);
  if (state.takeover) {
    hubSend("takeover_event", stored);
  }
}

async function broadcastTakeover(on) {
  try {
    const tabs = await chrome.tabs.query({});
    for (const t of tabs || []) {
      if (!t.id) continue;
      const origin = originOf(t.url || "");
      if (!originAllowed(origin)) continue;
      try {
        await chrome.tabs.sendMessage(t.id, { type: "takeover", on: !!on });
      } catch {
        /* content script may not be ready */
      }
    }
  } catch {
    /* ignore */
  }
}

function handleHubMessage(env) {
  if (!env || typeof env !== "object") return;
  const type = env.type;
  const data = env.data || {};
  if (type === "takeover_start") {
    state.takeover = true;
    state.recording = true;
    broadcastTakeover(true);
    (async () => {
      const tab = await activeHttpTab();
      if (!tab) return;
      record({
        kind: "navigation",
        url: tab.url,
        origin: originOf(tab.url),
        ts: new Date().toISOString(),
        frame: "main",
      });
      if (!originAllowed(originOf(tab.url))) {
        noteIgnored(originOf(tab.url));
        return;
      }
      await inject(tab.id, tab.url);
      await requestPageState(tab.id, tab.url);
      try {
        await chrome.tabs.sendMessage(tab.id, { type: "takeover", on: true });
      } catch {
        /* ignore */
      }
    })();
    return;
  }
  if (type === "takeover_stop") {
    state.takeover = false;
    state.recording = false;
    broadcastTakeover(false);
    return;
  }
  if (type === "resume") {
    state.takeover = false;
    broadcastTakeover(false);
  }
}

function noteIgnored(origin) {
  if (isHttpOrigin(origin) && !originAllowed(origin)) {
    state.ignoredOrigin = origin;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  loadConfig();
});

if (chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    loadConfig();
  });
}

chrome.webNavigation.onCommitted.addListener(async (d) => {
  if (d.frameId !== 0) return;
  await loadConfig();
  if (!isHttpUrl(d.url)) return;
  const origin = originOf(d.url);
  if (state.recording || state.takeover) {
    record({
      kind: "navigation",
      url: d.url,
      origin,
      ts: new Date().toISOString(),
      frame: "main",
    });
  }
  if (!originAllowed(origin)) {
    if (state.recording || state.takeover) noteIgnored(origin);
    return;
  }
  await inject(d.tabId, d.url);
  await requestPageState(d.tabId, d.url);
});

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== "complete") return;
  await loadConfig();
  const url = tab.url || "";
  const origin = originOf(url);
  if (!originAllowed(origin)) {
    if (state.recording) noteIgnored(origin);
    return;
  }
  await inject(tabId, url);
  await requestPageState(tabId, url);
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    await loadConfig();
    const type = msg && msg.type;
    if (type === "allowOrigin?") {
      const origin = msg.origin || originOf(sender.url || "");
      sendResponse(originAllowed(origin));
      return;
    }
    if (type === "page_state") {
      const origin = (msg.state && msg.state.origin) || originOf(sender.url || sender.tab?.url || "");
      if (!originAllowed(origin)) {
        sendResponse({ ok: false, error: "origin not allowlisted" });
        return;
      }
      const sent = forwardPageState(msg.state, sender.url || "");
      sendResponse({ ok: sent });
      return;
    }
    if (type === "record") {
      const ev = msg.event;
      const kind = ev && String(ev.kind || "").toLowerCase();
      if (isNavigationKind(kind)) {
        const url = String((ev && ev.url) || sender.url || sender.tab?.url || "");
        if (!isHttpUrl(url)) {
          sendResponse({ ok: false, error: "blocked scheme" });
          return;
        }
        record({ ...(typeof ev === "object" && ev ? ev : {}), kind: "navigation", url });
        sendResponse({ ok: true, n: state.events.length, takeover: state.takeover });
        return;
      }
      const origin = originOf(sender.url || sender.tab?.url || "");
      if (!originAllowed(origin)) {
        sendResponse({ ok: false, error: "origin not allowlisted" });
        return;
      }
      record(msg.event);
      sendResponse({ ok: true, n: state.events.length, takeover: state.takeover });
      if (msg.event && msg.event.unstable && !state.takeover) {
        maybeAssist(msg.event);
      }
      return;
    }
    if (type === "status") {
      const hub = hubStatus();
      sendResponse({
        ok: true,
        recording: state.recording,
        takeover: state.takeover,
        n: state.events.length,
        goal: state.goal,
        allowlist: [...state.allowlist],
        hasSession: Boolean(state.cfg),
        allowSecrets: Boolean(state.cfg && state.cfg.allowSecrets),
        exportOrigin: exportOrigin(),
        ignoredOrigin: state.ignoredOrigin || "",
        hub: hub.hub,
        hubPaired: Boolean(hub.paired),
        hubSessionId: hub.sessionId || "",
      });
      return;
    }
    if (type === "start") {
      if (!state.cfg) {
        sendResponse({
          ok: false,
          error: "start via cloakcli teach start (session.json missing)",
        });
        return;
      }
      state.recording = true;
      const tab = await activeHttpTab();
      if (tab) {
        const origin = originOf(tab.url);
        if (isHttpUrl(tab.url)) {
          record({ kind: "navigation", url: tab.url, origin });
        }
        if (originAllowed(origin)) {
          await inject(tab.id, tab.url);
          await requestPageState(tab.id, tab.url);
        } else {
          noteIgnored(origin);
        }
      }
      sendResponse({ ok: true, recording: true, n: state.events.length });
      return;
    }
    if (type === "stop") {
      state.recording = false;
      sendResponse({ ok: true, recording: false, n: state.events.length });
      return;
    }
    if (type === "goal") {
      const text = String(msg.text || "").trim();
      state.goal = text;
      if (text) {
        record({ kind: "goal", text });
      }
      sendResponse({ ok: true, goal: state.goal });
      return;
    }
    if (type === "approveOrigin") {
      /* Popup-only: a content script must not grow the allowlist. */
      if (sender && sender.tab) {
        sendResponse({ ok: false, error: "approve from the extension popup" });
        return;
      }
      let origin = String(msg.origin || "").trim();
      if (!origin) {
        const tab = await activeHttpTab();
        origin = tab ? originOf(tab.url) : "";
      }
      if (!isHttpOrigin(origin)) {
        sendResponse({ ok: false, error: "not an http(s) origin" });
        return;
      }
      if (exportOrigin() && origin === originOf(exportOrigin())) {
        sendResponse({ ok: false, error: "cannot allowlist the export server" });
        return;
      }
      const ok = await requestOrigin(origin);
      if (!ok) {
        sendResponse({ ok: false, error: "origin rejected" });
        return;
      }
      const tabs = await chrome.tabs.query({});
      for (const t of tabs || []) {
        if (!t.id || originOf(t.url || "") !== origin) continue;
        if (state.recording) {
          record({ kind: "navigation", url: t.url });
        }
        await inject(t.id, t.url);
        await requestPageState(t.id, t.url);
      }
      hubSend("allowlist_update", { origin });
      sendResponse({
        ok: true,
        origin,
        allowlist: [...state.allowlist],
      });
      return;
    }
    if (type === "export") {
      const result = await doExport(msg.name, msg.goal, msg.smartOptimize);
      sendResponse(result);
      return;
    }
    sendResponse({ ok: false, error: "unknown message" });
  })();
  return true;
});

async function activeHttpTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const t = tabs && tabs[0];
  if (!t || !t.id || !t.url) return null;
  if (!isHttpOrigin(originOf(t.url))) return null;
  return t;
}

async function maybeAssist(event) {
  /* TEACH PATH: optional mid-record assist. Server caps LLM at 2 and may defer. */
  if (!state.cfg || state.assistInFlight || !event) return;
  const origin = (state.cfg.exportOrigin || "").replace(/\/$/, "");
  if (!origin) return;
  state.assistInFlight = true;
  try {
    const r = await fetch(`${origin}/assist`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CloakCLI-Token": state.cfg.token,
      },
      body: JSON.stringify({
        selector: event.selector,
        selectors: event.selectors || [],
        field: event.field || null,
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (data && data.ok && Array.isArray(data.selectors) && data.selectors.length) {
      const last = state.events[state.events.length - 1];
      if (last && last.selector === event.selector) {
        last.selectors = data.selectors;
      }
    }
  } catch {
    /* assist is optional; recording continues */
  } finally {
    state.assistInFlight = false;
  }
}

async function doExport(name, goal, smartOptimize) {
  if (!state.cfg) {
    return { ok: false, error: "start via cloakcli teach start" };
  }
  const n = String(name || "").trim();
  if (!n) {
    return { ok: false, error: "skill name required" };
  }
  const g = String(goal || state.goal || "").trim();
  const url = `${state.cfg.exportOrigin.replace(/\/$/, "")}/export`;
  const smart =
    typeof smartOptimize === "boolean"
      ? smartOptimize
      : state.cfg.smartOptimize !== false;
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CloakCLI-Token": state.cfg.token,
      },
      body: JSON.stringify({
        name: n,
        goal: g || undefined,
        events: state.events,
        smartOptimize: smart,
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      return { ok: false, error: data.error || `export HTTP ${r.status}` };
    }
    return { ok: true, path: data.path };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/* Service-worker start (install, browser start, or idle-kill restart). */
loadConfig();
