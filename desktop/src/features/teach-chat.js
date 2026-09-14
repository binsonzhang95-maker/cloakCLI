import {
  jobCancel,
  jobStart,
  listen,
  teachChatCancel,
  teachChatConfirm,
  teachChatSend,
  teachChatStart,
  teachChatStatus,
  teachChatStop,
  teachResumeHint,
} from "../api.js";
import { redactEventPayload, redactText, summarizeUserMessage } from "../redact.js";
import { currentProfile, getState, setCatalog, upsertRun } from "../store.js";
import { openRunFromChat, runFromJobEvent } from "./runs.js";

const WELCOME = [
  {
    role: "system",
    text: "Teach Chat — live cloakcli teach-chat via JSONL. Playwright / LLM stay in Rust. Start a session, then send a goal. Reconnect restores the M2 snapshot (new hub port).",
  },
];

const chat = {
  messages: WELCOME.map((m) => ({ ...m, ts: Date.now() })),
  session: null,
  status: null,
  job: null,
  tools: [],
  connecting: false,
  sending: false,
  error: null,
  url: "",
  spawnBrowser: false,
  fleetHint: null,
  resumeHint: null,
};

let listening = false;
let lastRoot = null;
let lastMsgSig = "";
let lastJobSig = "";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function lamp(on) {
  return on ? "lamp ok" : "lamp";
}

export async function ensureChatEvents() {
  if (listening) return;
  listening = true;
  await listen("teach_chat_event", (ev) => {
    onTeachEvent(ev?.payload);
  });
}

export function getChatState() {
  return chat;
}

export function resetChatState() {
  chat.messages = WELCOME.map((m) => ({ ...m, ts: Date.now() }));
  chat.session = null;
  chat.status = null;
  chat.job = null;
  chat.tools = [];
  chat.connecting = false;
  chat.sending = false;
  chat.error = null;
  chat.url = "";
  chat.spawnBrowser = false;
  chat.fleetHint = null;
  chat.resumeHint = null;
  lastMsgSig = "";
  lastJobSig = "";
}

export function onTeachEvent(rawPayload) {
  const payload = redactEventPayload(rawPayload);
  if (!payload || typeof payload !== "object") return;
  const kind = payload.kind;
  const text = payload.text;
  if (kind === "session") {
    chat.session = payload;
    chat.error = null;
  } else if (kind === "status") {
    chat.status = payload;
    if (Array.isArray(payload.tools)) chat.tools = payload.tools;
  } else if (kind === "resume") {
    if (Array.isArray(payload.messages) && payload.messages.length) {
      chat.messages = payload.messages.map((m) => ({
        role: m.role || "system",
        text: m.text || "",
        ts: Date.now(),
      }));
    }
    if (Array.isArray(payload.tools)) chat.tools = payload.tools;
    if (payload.note) {
      const last = chat.messages[chat.messages.length - 1];
      if (!(last && last.role === "system" && last.text === payload.note)) {
        chat.messages.push({ role: "system", text: payload.note, ts: Date.now() });
      }
    }
    if (payload.hub_resume === "new_hub") {
      chat.status = {
        ...(chat.status || {}),
        hub_resume: "new_hub",
        phase: payload.phase || "chat",
      };
    }
  } else if (kind === "assistant_delta") {
    const chunk = text || "";
    const last = chat.messages[chat.messages.length - 1];
    if (last && last.role === "assistant" && last.streaming) {
      last.text += chunk;
    } else {
      chat.messages.push({
        role: "assistant",
        text: chunk,
        streaming: true,
        ts: Date.now(),
      });
    }
    chat.sending = true;
  } else if (kind === "user" || kind === "assistant" || kind === "system") {
    const role = payload.role || kind;
    const body = text || "";
    const last = chat.messages[chat.messages.length - 1];
    if (kind === "assistant" && last && last.role === "assistant" && last.streaming) {
      last.text = body || last.text;
      last.streaming = false;
    } else if (!(last && last.role === role && last.text === body && !last.streaming)) {
      if (last && last.role === role && last.streaming) {
        last.text = body || last.text;
        last.streaming = false;
      } else {
        chat.messages.push({ role, text: body, ts: Date.now() });
      }
    }
  } else if (kind === "tool") {
    chat.tools = Array.isArray(payload.tools) ? payload.tools : [];
  } else if (kind === "job") {
    chat.job = {
      job_id: payload.job_id,
      state: payload.state,
      summary: typeof payload.summary === "string" ? redactText(payload.summary) : payload.summary,
      error: typeof payload.error === "string" ? redactText(payload.error) : payload.error,
    };
    chat.sending = payload.state === "running" || payload.state === "needs_confirm";
    if (payload.state === "cancelled" || payload.state === "done" || payload.state === "failed") {
      const last = chat.messages[chat.messages.length - 1];
      if (last && last.streaming) last.streaming = false;
    }
    const rec = runFromJobEvent(payload, currentProfile()?.name);
    if (rec) {
      rec.summary = typeof rec.summary === "string" ? redactText(rec.summary) : rec.summary;
      rec.error = typeof rec.error === "string" ? redactText(rec.error) : rec.error;
      upsertRun(rec);
    }
  } else if (kind === "error") {
    chat.error = redactText(payload.message || payload.code || "error");
    chat.messages.push({
      role: "system",
      text: chat.error,
      ts: Date.now(),
    });
    chat.sending = false;
  } else if (kind === "closed") {
    chat.session = null;
    chat.status = { ...(chat.status || {}), running: false, hub: false, busy: false };
    chat.sending = false;
    const last = chat.messages[chat.messages.length - 1];
    if (!(last && last.role === "system" && String(last.text).startsWith("session closed"))) {
      chat.messages.push({
        role: "system",
        text: "session closed — Reconnect restores transcript (new hub port)",
        ts: Date.now(),
      });
    }
    refreshResumeHint();
  }
  paint();
}

export function renderChat(root) {
  lastRoot = root;
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">TEACH CHAT</div>
        <div class="page-title">Session</div>
      </div>
      <div class="page-sub" id="chat-sub"></div>
    </div>
    <div class="chat-pair" id="chat-pair">
      <div class="pair-lamps">
        <span class="lamp-wrap"><span id="chat-lamp-hub" class="lamp"></span>teach hub</span>
        <span class="lamp-wrap"><span id="chat-lamp-worker" class="lamp"></span>worker</span>
        <span class="lamp-wrap"><span id="chat-lamp-ext" class="lamp"></span>ext</span>
        <span class="pair-meta" id="chat-phase">stopped</span>
      </div>
      <div class="pair-actions">
        <input id="chat-url" type="text" spellcheck="false" placeholder="https://… (optional)" />
        <label class="check-row compact" title="Headed CloakBrowser (needs a display)">
          <input id="chat-browser" type="checkbox" />
          browser
        </label>
        <button type="button" id="chat-start">Start</button>
        <button type="button" class="ghost" id="chat-end" disabled>End</button>
      </div>
      <div class="pair-ids" id="chat-ids">start a session to pair the extension / worker</div>
    </div>
    <div class="chat">
      <div class="resume-banner hidden" id="chat-resume"></div>
      <div class="chat-stream" id="chat-stream"></div>
      <div class="job-card" id="chat-job"></div>
      <form class="chat-input" id="chat-form">
        <textarea id="chat-text" rows="2" placeholder="Goal… Enter to send, Shift+Enter newline"></textarea>
        <button type="submit" id="chat-send">Send</button>
        <button type="button" class="ghost" id="chat-stop">Stop</button>
      </form>
    </div>
  `;
  const form = root.querySelector("#chat-form");
  const area = root.querySelector("#chat-text");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    send(area);
  });
  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(area);
    }
  });
  root.querySelector("#chat-stop").addEventListener("click", () => stopTurn());
  root.querySelector("#chat-start").addEventListener("click", () => startSession(true));
  root.querySelector("#chat-end").addEventListener("click", () => endSession());
  root.querySelector("#chat-url").addEventListener("change", (e) => {
    chat.url = e.target.value.trim();
  });
  root.querySelector("#chat-browser").addEventListener("change", (e) => {
    chat.spawnBrowser = e.target.checked;
  });
  const url = root.querySelector("#chat-url");
  const br = root.querySelector("#chat-browser");
  if (url && !url.value) url.value = chat.url;
  if (br) br.checked = chat.spawnBrowser;
  lastMsgSig = "";
  lastJobSig = "";
  paint();
}

export function updateChatChrome() {
  paint();
}

function paint() {
  if (typeof document === "undefined") return;
  const root = lastRoot || document.getElementById("page-chat");
  if (!root || !root.querySelector("#chat-stream")) return;
  const profile = currentProfile();
  const skill = getState().selectedSkill;
  const st = chat.status;
  const running = Boolean(chat.session);
  const sub = root.querySelector("#chat-sub");
  if (sub) {
    sub.textContent = `context ${profile?.name || "—"} · skill ${skill || "—"} · ${
      running ? "live" : "idle"
    }`;
  }
  const hubL = root.querySelector("#chat-lamp-hub");
  const wrkL = root.querySelector("#chat-lamp-worker");
  const extL = root.querySelector("#chat-lamp-ext");
  if (hubL) hubL.className = lamp(st?.hub || running);
  if (wrkL) wrkL.className = lamp(st?.worker);
  if (extL) extL.className = lamp(st?.extension);
  const phase = root.querySelector("#chat-phase");
  if (phase) phase.textContent = st?.phase || (running ? "chat" : "stopped");
  const startBtn = root.querySelector("#chat-start");
  if (startBtn) startBtn.textContent = running ? "Reconnect" : "Start";
  const endBtn = root.querySelector("#chat-end");
  if (endBtn) endBtn.disabled = !running;
  const ids = root.querySelector("#chat-ids");
  if (ids) {
    const sess = chat.session;
    ids.textContent = sess
      ? `session ${sess.session_id || "—"} · pair ${sess.pairing_id || "—"} · code ${
          sess.pairing_code || "—"
        } · ${sess.hub_url || ""}`
      : chat.error || "start a session to pair the extension / worker";
  }
  paintResume();
  paintMessages();
  paintJob();
  const stop = root.querySelector("#chat-stop");
  if (stop) stop.disabled = !(chat.sending || st?.busy);
  const sendBtn = root.querySelector("#chat-send");
  if (sendBtn) sendBtn.disabled = chat.connecting;
}

function paintResume() {
  const el = document.getElementById("chat-resume");
  if (!el) return;
  const hint = chat.resumeHint || getState().resumeHint;
  const running = Boolean(chat.session);
  if (!hint?.present || running) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `
    <div>
      <strong>Resume available</strong>
      · profile ${esc(hint.profile || "—")}
      · ${esc(String(hint.message_count ?? 0))} messages
      · ${esc(hint.relative_path || "data/teach/events-snapshot.json")}
    </div>
    <div class="muted">${esc(hint.note || "Reconnect restores transcript. Hub binds a new port.")}</div>
    <button type="button" id="chat-resume-btn">Reconnect</button>
  `;
  el.querySelector("#chat-resume-btn")?.addEventListener("click", () => startSession(true));
}

function paintMessages() {
  const stream = document.getElementById("chat-stream");
  if (!stream) return;
  const last = chat.messages[chat.messages.length - 1];
  const sig = `${chat.messages.length}:${last?.role || ""}:${last?.text || ""}:${last?.streaming ? 1 : 0}`;
  if (sig === lastMsgSig && stream.childElementCount) return;
  lastMsgSig = sig;
  stream.innerHTML = chat.messages
    .map(
      (m) => `
      <article class="msg ${esc(m.role)}${m.streaming ? " is-streaming" : ""}">
        <div class="msg-role">${esc(m.role)}</div>
        <div class="msg-body">${esc(m.text)}</div>
      </article>`,
    )
    .join("");
  stream.scrollTop = stream.scrollHeight;
}

function paintJob() {
  const el = document.getElementById("chat-job");
  if (!el) return;
  const job = chat.job;
  const tools = chat.tools || [];
  const fleet = chat.fleetHint;
  const toolLines = tools
    .map((t) => `<div class="job-tool"><span>${esc(t.status)}</span>${esc(t.summary)}</div>`)
    .join("");
  const sig = JSON.stringify({ job, tools, fleet });
  if (sig === lastJobSig && el.childElementCount) return;
  lastJobSig = sig;
  if (!job) {
    el.className = "job-card";
    el.innerHTML = `<strong>idle</strong> · no in-flight teach turn${
      fleet ? `<div class="job-hint">${esc(fleet)}</div>` : ""
    }`;
    return;
  }
  const needs = job.state === "needs_confirm";
  el.className = `job-card is-${esc(job.state || "idle")}`;
  el.innerHTML = `
    <div class="job-head">
      <strong>${esc(job.state || "idle")}</strong>
      <span>${esc(job.job_id || "")}</span>
      <button type="button" class="ghost" id="job-history">History</button>
      ${
        job.state === "running" || needs
          ? `<button type="button" class="ghost" id="job-cancel">Cancel</button>`
          : ""
      }
      ${
        needs
          ? `<button type="button" id="job-yes">Confirm</button><button type="button" class="ghost" id="job-no">Reject</button>`
          : ""
      }
    </div>
    <div>${esc(job.summary || "")}${job.error ? ` · ${esc(job.error)}` : ""}</div>
    ${toolLines}
  `;
  el.querySelector("#job-history")?.addEventListener("click", () =>
    openRunFromChat(job.job_id),
  );
  el.querySelector("#job-cancel")?.addEventListener("click", () => stopTurn());
  el.querySelector("#job-yes")?.addEventListener("click", () => confirmNav(true));
  el.querySelector("#job-no")?.addEventListener("click", () => confirmNav(false));
}

async function startSession(force) {
  const profile = currentProfile();
  if (!profile) {
    chat.error = "select a profile first";
    paint();
    return null;
  }
  if (chat.session && !force) return chat.session;
  chat.connecting = true;
  chat.error = null;
  paint();
  const urlEl = document.getElementById("chat-url");
  if (urlEl) chat.url = urlEl.value.trim();
  const br = document.getElementById("chat-browser");
  if (br) chat.spawnBrowser = br.checked;
  try {
    const dto = await teachChatStart(profile.name, chat.url || null, chat.spawnBrowser);
    chat.session = dto;
    chat.connecting = false;
    paint();
    const hint = await jobStart();
    chat.fleetHint = hint?.wired ? null : hint?.hint || null;
    paint();
    return dto;
  } catch (err) {
    chat.connecting = false;
    chat.error = redactText(String(err));
    chat.messages.push({ role: "system", text: chat.error, ts: Date.now() });
    paint();
    return null;
  }
}

async function endSession() {
  try {
    await teachChatStop();
  } catch {
    // already gone
  }
  chat.session = null;
  chat.sending = false;
  paint();
}

async function stopTurn() {
  try {
    await teachChatCancel();
    await jobCancel();
  } catch (err) {
    chat.error = redactText(String(err));
  }
  chat.sending = false;
  paint();
}

async function confirmNav(yes) {
  try {
    await teachChatConfirm(yes);
  } catch (err) {
    chat.error = redactText(String(err));
    paint();
  }
}

async function send(area) {
  const raw = (area.value || "").trim();
  if (!raw) return;
  const summary = summarizeUserMessage(raw);
  area.value = "";
  chat.messages.push({ role: "user", text: summary.display, ts: Date.now() });
  chat.sending = true;
  paint();
  try {
    if (!chat.session) {
      const started = await startSession(false);
      if (!started) {
        chat.sending = false;
        paint();
        return;
      }
    }
    const profile = currentProfile();
    const skill = getState().selectedSkill;
    await teachChatSend(raw, profile?.name, skill || null);
  } catch (err) {
    chat.sending = false;
    chat.messages.push({
      role: "system",
      text: redactText(String(err)),
      ts: Date.now(),
    });
    paint();
  }
}

export async function refreshTeachStatus() {
  try {
    const st = await teachChatStatus();
    if (st) {
      chat.status = { ...(chat.status || {}), ...st };
      if (!st.running) chat.session = null;
    }
    paint();
  } catch {
    // ignore
  }
}

export async function refreshResumeHint() {
  try {
    const hint = await teachResumeHint();
    chat.resumeHint = hint;
    setCatalog({ resumeHint: hint });
    paint();
  } catch {
    // home unset or command unavailable
  }
}
