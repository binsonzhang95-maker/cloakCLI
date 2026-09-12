/* CloakCLI Teach — service worker.
 * Origin allowlist only (no default <all_urls> content_scripts).
 * Events stay in memory until export. Secrets are not written to storage.
 */

const state = {
  cfg: null,
  recording: false,
  events: [],
  goal: "",
  allowlist: new Set(),
};

function isHttpOrigin(origin) {
  try {
    const u = new URL(origin);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
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
  try {
    const r = await fetch(chrome.runtime.getURL("session.json"));
    if (!r.ok) return null;
    state.cfg = await r.json();
    for (const o of state.cfg.allowOrigins || []) {
      if (isHttpOrigin(o)) state.allowlist.add(o);
    }
    return state.cfg;
  } catch {
    return null;
  }
}

function addOrigin(origin) {
  if (!isHttpOrigin(origin)) return false;
  if (exportOrigin() && origin === originOf(exportOrigin())) return false;
  state.allowlist.add(origin);
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
  if (!originAllowed(origin)) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      files: ["content.js"],
    });
  } catch {
    /* tab may be chrome:// or gone */
  }
}

function record(event) {
  if (!state.recording) return;
  if (!event || typeof event !== "object") return;
  const kind = String(event.kind || "").toLowerCase();
  if (kind === "navigation") {
    const url = String(event.url || "");
    const origin = originOf(url);
    if (!originAllowed(origin) && !addOrigin(origin)) return;
  }
  if (kind === "click" || kind === "input") {
    /* content script already checked origin */
  }
  state.events.push(event);
}

chrome.runtime.onInstalled.addListener(() => {
  loadConfig();
});

chrome.webNavigation.onCommitted.addListener(async (d) => {
  if (d.frameId !== 0) return;
  await loadConfig();
  const origin = originOf(d.url);
  if (!isHttpOrigin(origin)) return;
  if (state.recording) {
    await requestOrigin(origin);
    record({ kind: "navigation", url: d.url });
    await inject(d.tabId, d.url);
  } else if (originAllowed(origin)) {
    await inject(d.tabId, d.url);
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== "complete") return;
  await loadConfig();
  const url = tab.url || "";
  const origin = originOf(url);
  if (state.recording && isHttpOrigin(origin)) {
    await requestOrigin(origin);
  }
  if (originAllowed(origin)) {
    await inject(tabId, url);
  }
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
    if (type === "record") {
      const origin = originOf(sender.url || sender.tab?.url || "");
      if (!originAllowed(origin)) {
        sendResponse({ ok: false, error: "origin not allowlisted" });
        return;
      }
      record(msg.event);
      sendResponse({ ok: true, n: state.events.length });
      return;
    }
    if (type === "status") {
      sendResponse({
        ok: true,
        recording: state.recording,
        n: state.events.length,
        goal: state.goal,
        allowlist: [...state.allowlist],
        hasSession: Boolean(state.cfg),
        allowSecrets: Boolean(state.cfg && state.cfg.allowSecrets),
        exportOrigin: exportOrigin(),
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
        await requestOrigin(originOf(tab.url));
        record({ kind: "navigation", url: tab.url });
        await inject(tab.id, tab.url);
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
    if (type === "export") {
      const result = await doExport(msg.name, msg.goal);
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

async function doExport(name, goal) {
  if (!state.cfg) {
    return { ok: false, error: "start via cloakcli teach start" };
  }
  const n = String(name || "").trim();
  if (!n) {
    return { ok: false, error: "skill name required" };
  }
  const g = String(goal || state.goal || "").trim();
  const url = `${state.cfg.exportOrigin.replace(/\/$/, "")}/export`;
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
