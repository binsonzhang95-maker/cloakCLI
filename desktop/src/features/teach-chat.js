import { mockAckPreview, summarizeUserMessage } from "../redact.js";
import { currentProfile, getState } from "../store.js";

const MOCK_WELCOME = [
  {
    role: "system",
    text: "Teach Chat M1 — local mock. Live hub / LLM / Playwright stay in Rust + worker (M2).",
  },
  {
    role: "assistant",
    text: "Describe a flow to teach. This pane records locally only; send is an event-protocol mock (`teach_chat_send` → `teach_chat_event`).",
  },
];

const messages = MOCK_WELCOME.map((m) => ({ ...m, ts: Date.now() }));
let streaming = false;

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function renderChat(root) {
  const profile = currentProfile();
  const jobs = getState().status?.jobs_running ?? 0;
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">TEACH CHAT</div>
        <div class="page-title">Session</div>
      </div>
      <div class="page-sub">context ${esc(profile?.name || "—")} · mock protocol</div>
    </div>
    <div class="chat">
      <div class="chat-stream" id="chat-stream"></div>
      <div class="job-card" id="chat-job">
        ${
          jobs > 0
            ? `<strong>${jobs}</strong> running job(s) — progress events land here in M2.`
            : "<strong>idle</strong> · no running jobs (catalog count)"
        }
      </div>
      <form class="chat-input" id="chat-form">
        <textarea id="chat-text" rows="2" placeholder="Message… Enter to send, Shift+Enter newline"></textarea>
        <button type="submit" id="chat-send">Send</button>
        <button type="button" class="ghost" id="chat-stop" ${streaming ? "" : "disabled"}>Stop</button>
      </form>
    </div>
  `;
  paintMessages();
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
  root.querySelector("#chat-stop").addEventListener("click", () => {
    streaming = false;
    messages.push({
      role: "system",
      text: "stop ignored — no live stream in M1",
      ts: Date.now(),
    });
    paintMessages();
  });
}

function paintMessages() {
  const stream = document.getElementById("chat-stream");
  if (!stream) return;
  stream.innerHTML = messages
    .map(
      (m) => `
      <article class="msg ${esc(m.role)}">
        <div class="msg-role">${esc(m.role)}</div>
        <div class="msg-body">${esc(m.text)}</div>
      </article>`,
    )
    .join("");
  stream.scrollTop = stream.scrollHeight;
}

function send(area) {
  const raw = (area.value || "").trim();
  if (!raw) return;
  const summary = summarizeUserMessage(raw);
  area.value = "";
  messages.push({ role: "user", text: summary.display, ts: Date.now() });
  streaming = true;
  paintMessages();
  const stop = document.getElementById("chat-stop");
  if (stop) stop.disabled = false;
  // Mock teach_chat_event — structured; never echoes raw input or secrets.
  window.setTimeout(() => {
    streaming = false;
    messages.push({
      role: "assistant",
      text: mockAckPreview(summary),
      ts: Date.now(),
    });
    paintMessages();
    const s = document.getElementById("chat-stop");
    if (s) s.disabled = true;
  }, 280);
}
