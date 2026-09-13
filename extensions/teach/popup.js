function $(id) {
  return document.getElementById(id);
}

function send(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (r) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }
      resolve(r || { ok: false, error: "no response" });
    });
  });
}

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

function currentTab() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      resolve((tabs && tabs[0]) || null);
    });
  });
}

function setMsg(text, cls) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = cls || "muted";
}

async function refresh() {
  const s = await send({ type: "status" });
  if (!s.ok && !s.hasSession && s.error) {
    $("session").textContent = s.error;
    $("status").textContent = "not attached";
    return s;
  }
  $("session").textContent = s.hasSession
    ? "session: cloakcli teach start"
    : "start via cloakcli teach start (no session.json)";
  $("status").textContent = s.recording
    ? `recording · ${s.n} event(s)`
    : `stopped · ${s.n} event(s)`;
  $("allow").textContent = (s.allowlist || []).length
    ? "origins: " + s.allowlist.join(", ")
    : "origins: (none — use --url or Allow this origin)";

  const tab = await currentTab();
  const origin = originOf((tab && tab.url) || "");
  const allowed = origin && (s.allowlist || []).includes(origin);
  const cur = $("current");
  if (!origin) {
    cur.textContent = "this tab: (not http(s))";
    cur.className = "muted";
  } else if (allowed) {
    cur.textContent = "this tab: " + origin + " (allowed)";
    cur.className = "ok";
  } else {
    const ignored =
      s.ignoredOrigin && s.ignoredOrigin === origin
        ? " — not recorded until you Allow"
        : " (not allowed)";
    cur.textContent = "this tab: " + origin + ignored;
    cur.className = "warn";
  }
  if (s.goal && !$("goal").value) $("goal").value = s.goal;
  return s;
}

$("record").addEventListener("click", async () => {
  const r = await send({ type: "start" });
  setMsg(r.ok ? "recording" : r.error || "failed", r.ok ? "ok" : "err");
  await refresh();
});

$("stop").addEventListener("click", async () => {
  const r = await send({ type: "stop" });
  setMsg(r.ok ? "stopped" : r.error || "failed", r.ok ? "ok" : "err");
  await refresh();
});

$("goalbtn").addEventListener("click", async () => {
  const r = await send({ type: "goal", text: $("goal").value });
  setMsg(r.ok ? `goal: ${r.goal || "(empty)"}` : r.error || "failed", r.ok ? "ok" : "err");
});

$("allowbtn").addEventListener("click", async () => {
  const tab = await currentTab();
  const origin = originOf((tab && tab.url) || "");
  const r = await send({ type: "approveOrigin", origin });
  setMsg(
    r.ok ? "allowed " + (r.origin || origin) : r.error || "failed",
    r.ok ? "ok" : "err"
  );
  await refresh();
});

$("export").addEventListener("click", async () => {
  const name = $("name").value.trim();
  const goal = $("goal").value.trim();
  if (goal) await send({ type: "goal", text: goal });
  const smart = $("smart") ? $("smart").checked : true;
  const r = await send({ type: "export", name, goal, smartOptimize: smart });
  if (r.ok) {
    setMsg("exported " + (r.path || ""), "ok");
  } else {
    setMsg(r.error || "export failed", "err");
  }
  await refresh();
});

refresh();
