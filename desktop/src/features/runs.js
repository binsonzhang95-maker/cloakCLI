import { getState, selectRun, setRoute } from "../store.js";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtTime(unix) {
  const n = Number(unix) || 0;
  if (!n) return "—";
  try {
    return new Date(n * 1000).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return String(n);
  }
}

export function runFromJobEvent(payload, profile) {
  if (!payload || typeof payload !== "object") return null;
  const jobId = payload.job_id || "pending";
  return {
    id: `teach:${jobId}`,
    kind: "teach",
    job_id: jobId,
    profile: profile || "",
    skill: payload.skill || "",
    state: payload.state || "unknown",
    summary: payload.summary || "",
    error: payload.error || null,
    updated_at: Math.floor(Date.now() / 1000),
    source: "teach_chat",
  };
}

export function openRunFromChat(jobId) {
  if (jobId) selectRun(`teach:${jobId}`);
  setRoute("runs");
}

export function renderRuns(root) {
  const { runs, selectedRun, status } = getState();
  const list = Array.isArray(runs) ? runs : [];
  const running = status?.jobs_running ?? 0;
  const selected = list.find((r) => r.id === selectedRun) || list[0] || null;
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">RUNS / HISTORY</div>
        <div class="page-title">Teach + jobs</div>
      </div>
      <div class="page-sub">${running} fleet running · ${list.length} recent · redacted · open from Chat</div>
    </div>
    <div class="runs-split">
      <div class="table" id="runs-table"></div>
      <div class="runs-detail" id="runs-detail"></div>
    </div>
  `;
  const table = root.querySelector("#runs-table");
  const detail = root.querySelector("#runs-detail");
  if (!list.length) {
    table.innerHTML = `<div class="empty">No teach turns or job records yet. Finish a Teach Chat turn or open a run from the Chat job card.</div>`;
    detail.innerHTML = `<div class="empty">Select a run.</div>`;
    return;
  }
  table.innerHTML = list
    .map((r) => {
      const sel = selected && selected.id === r.id ? " is-selected" : "";
      return `
      <div class="row runs-row${sel}" data-id="${esc(r.id)}" role="button" tabindex="0">
        <div class="row-name">${esc(r.kind)} ${esc(r.job_id)}</div>
        <div class="row-meta">${esc(r.profile || "—")}</div>
        <div class="row-meta">${esc(r.skill || "—")}</div>
        <div class="row-meta">${esc(r.state)}</div>
      </div>`;
    })
    .join("");
  table.querySelectorAll(".row").forEach((row) => {
    row.addEventListener("click", () => selectRun(row.dataset.id));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectRun(row.dataset.id);
      }
    });
  });
  if (!selected) {
    detail.innerHTML = `<div class="empty">Select a run.</div>`;
    return;
  }
  detail.innerHTML = `
    <div class="kv">
      <span>id</span><span>${esc(selected.id)}</span>
      <span>kind</span><span>${esc(selected.kind)}</span>
      <span>job</span><span>${esc(selected.job_id)}</span>
      <span>state</span><span>${esc(selected.state)}</span>
      <span>profile</span><span>${esc(selected.profile || "—")}</span>
      <span>skill</span><span>${esc(selected.skill || "—")}</span>
      <span>source</span><span>${esc(selected.source || "—")}</span>
      <span>updated</span><span>${esc(fmtTime(selected.updated_at))}</span>
      <span>summary</span><span>${esc(selected.summary || "—")}</span>
      <span>error</span><span>${esc(selected.error || "—")}</span>
    </div>
    <p class="muted runs-note">Free-text is redacted. Job extracts and cookie values are never stored. Snapshot rows reconnect via Chat (new hub port).</p>
    ${
      selected.state === "resume_available"
        ? `<button type="button" id="runs-reconnect">Open Chat to reconnect</button>`
        : ""
    }
  `;
  detail.querySelector("#runs-reconnect")?.addEventListener("click", () => setRoute("chat"));
}
