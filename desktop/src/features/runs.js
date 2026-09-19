import { fleetPark, fleetRetry } from "../api.js";
import { redactText } from "../redact.js";
import { getState, selectRun, setRoute, setRunsTab } from "../store.js";

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
  const result = payload.result && typeof payload.result === "object" ? payload.result : null;
  const fleet = payload.kind === "fleet" || payload.source === "fleet";
  return {
    id: fleet ? `fleet:${jobId}` : `teach:${jobId}`,
    kind: fleet ? "fleet" : "teach",
    job_id: jobId,
    profile: profile || payload.profile || "",
    skill: payload.skill || payload.skill_id || (result && result.skill_id) || "",
    state: payload.state || "unknown",
    status: result && result.status ? result.status : "",
    label: result && result.label ? result.label : "",
    success: result ? !!result.success : null,
    retryable: result ? !!result.retryable : false,
    digest: (result && result.digest) || payload.skill_digest || "",
    summary: payload.summary || "",
    error: payload.error || payload.protocol_error || null,
    protocol_error: payload.protocol_error || null,
    updated_at: Math.floor(Date.now() / 1000),
    source: payload.source || "teach_chat",
    detail: null,
    disposition: payload.disposition || null,
  };
}

/** Group runs by skill_id. Columns are per-skill status/label — never a global email_confirmed field. */
export function partitionRunsBySkill(runs) {
  const list = Array.isArray(runs) ? runs : [];
  const by = {};
  for (const r of list) {
    const key = r.skill || "(none)";
    if (!by[key]) by[key] = [];
    by[key].push(r);
  }
  return by;
}

export function partitionLedgersBySkill(ledgers) {
  const list = Array.isArray(ledgers) ? ledgers : [];
  const by = {};
  for (const part of list) {
    const key = part.skill_id || "(none)";
    by[key] = part;
  }
  return by;
}

/** Display-only. Never infers success from scheduler state. */
export function businessChip(run) {
  if (!run || run.success !== true) {
    const label = run?.label || run?.status || "";
    if (run?.protocol_error) {
      return { cls: "chip warn", text: `protocol · ${run.protocol_error}` };
    }
    if (run?.retryable) {
      return { cls: "chip warn", text: label ? `retryable · ${label}` : "retryable" };
    }
    if (label) {
      return { cls: "chip", text: label };
    }
    return { cls: "chip", text: "no business result" };
  }
  const label = run.label || run.status || "success";
  return { cls: "chip ok", text: `success · ${label}` };
}

export function openRunFromChat(jobId) {
  if (jobId) selectRun(`teach:${jobId}`);
  setRoute("runs");
}

export function renderRuns(root) {
  const { runs, selectedRun, status, runsTab, ledgers } = getState();
  const list = Array.isArray(runs) ? runs : [];
  const running = status?.jobs_running ?? 0;
  const tab = runsTab === "ledger" ? "ledger" : "history";
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">RUNS</div>
        <div class="page-title">Execution + ledger</div>
      </div>
      <div class="page-sub">${running} fleet running · ${list.length} recent · scheduler state ≠ business status</div>
    </div>
    <div class="tabs" role="tablist">
      <button type="button" class="tab ${tab === "history" ? "is-active" : ""}" data-tab="history">Execution history</button>
      <button type="button" class="tab ${tab === "ledger" ? "is-active" : ""}" data-tab="ledger">Accounts / Ledger</button>
    </div>
    <div class="runs-split" id="runs-body"></div>
  `;
  root.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => setRunsTab(btn.dataset.tab));
  });
  const body = root.querySelector("#runs-body");
  if (tab === "ledger") {
    renderLedger(body, ledgers);
    return;
  }
  renderHistory(body, list, selectedRun);
}

function renderHistory(root, list, selectedRun) {
  const selected = list.find((r) => r.id === selectedRun) || list[0] || null;
  const parts = partitionRunsBySkill(list);
  const skillKeys = Object.keys(parts).sort();
  root.innerHTML = `
    <div class="table" id="runs-table"></div>
    <div class="runs-detail" id="runs-detail"></div>
  `;
  const table = root.querySelector("#runs-table");
  const detail = root.querySelector("#runs-detail");
  if (!list.length) {
    table.innerHTML = `<div class="empty">No teach turns or job records yet. Submit from Fleet or finish a Teach Chat turn.</div>`;
    detail.innerHTML = `<div class="empty">Select a run.</div>`;
    return;
  }
  table.innerHTML = skillKeys
    .map((skill) => {
      const rows = parts[skill]
        .map((r) => {
          const sel = selected && selected.id === r.id ? " is-selected" : "";
          const chip = businessChip(r);
          return `
      <div class="row runs-row${sel}" data-id="${esc(r.id)}" role="button" tabindex="0">
        <div class="row-name">${esc(r.kind)} ${esc(r.job_id)}</div>
        <div class="row-meta">${esc(r.profile || "—")}</div>
        <div class="row-meta">state ${esc(r.state)}</div>
        <div class="row-meta"><span class="${chip.cls}">${esc(chip.text)}</span></div>
      </div>`;
        })
        .join("");
      return `<div class="skill-part"><div class="skill-part-h">${esc(skill)}</div>${rows}</div>`;
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
  const chip = businessChip(selected);
  const canRetry = selected.retryable === true && selected.kind !== "teach";
  const canPark = selected.kind !== "teach" && selected.disposition !== "park";
  detail.innerHTML = `
    <div class="kv">
      <span>id</span><span>${esc(selected.id)}</span>
      <span>kind</span><span>${esc(selected.kind)}</span>
      <span>job</span><span>${esc(selected.job_id)}</span>
      <span>state</span><span>${esc(selected.state)}</span>
      <span>status</span><span class="${chip.cls}">${esc(chip.text)}</span>
      <span>label</span><span>${esc(selected.label || "—")}</span>
      <span>success</span><span>${selected.success === true ? "true" : selected.success === false ? "false" : "—"}</span>
      <span>retryable</span><span>${selected.retryable ? "true" : "false"}</span>
      <span>profile</span><span>${esc(selected.profile || "—")}</span>
      <span>geo</span><span>${esc(selected.geo || "—")}</span>
      <span>account</span><span>${esc(selected.account_id || "—")}</span>
      <span>skill</span><span>${esc(selected.skill || "—")}</span>
      <span>digest</span><span class="mono">${esc(selected.digest || "—")}</span>
      <span>source</span><span>${esc(selected.source || "—")}</span>
      <span>disposition</span><span>${esc(selected.disposition || "—")} <span class="muted">(ops, not a skill status)</span></span>
      <span>updated</span><span>${esc(fmtTime(selected.updated_at))}</span>
      <span>summary</span><span>${esc(selected.summary || "—")}</span>
      <span>error</span><span>${esc(selected.error || selected.protocol_error || "—")}</span>
    </div>
    <pre class="runs-json">${esc(selected.detail || "{}")}</pre>
    <p class="muted runs-note">Business label/success/retryable come from the job digest statuses. Frontend does not infer success from scheduler state. Secrets are stripped before this DTO.</p>
    <div class="row-actions">
      ${canRetry ? `<button type="button" id="runs-retry">Retry (same account+profile)</button>` : ""}
      ${canPark ? `<button type="button" class="ghost" id="runs-park">Park / 先放</button>` : ""}
      ${
        selected.state === "resume_available"
          ? `<button type="button" id="runs-reconnect">Open Chat to reconnect</button>`
          : ""
      }
      ${!canRetry && selected.kind !== "teach" && selected.status ? `<button type="button" class="ghost" id="runs-why">View reason</button>` : ""}
    </div>
    <p class="setup-error" id="runs-action-err"></p>
  `;
  detail.querySelector("#runs-reconnect")?.addEventListener("click", () => setRoute("chat"));
  detail.querySelector("#runs-retry")?.addEventListener("click", async () => {
    const errEl = detail.querySelector("#runs-action-err");
    try {
      await fleetRetry(selected.job_id);
      errEl.textContent = "retry submitted (same profile/account/digest)";
    } catch (err) {
      errEl.textContent = redactText(String(err));
    }
  });
  detail.querySelector("#runs-park")?.addEventListener("click", async () => {
    const errEl = detail.querySelector("#runs-action-err");
    try {
      await fleetPark(selected.job_id, "ops park");
      errEl.textContent = "parked (ops disposition; skill status unchanged)";
    } catch (err) {
      errEl.textContent = redactText(String(err));
    }
  });
  detail.querySelector("#runs-why")?.addEventListener("click", () => {
    const errEl = detail.querySelector("#runs-action-err");
    errEl.textContent = selected.protocol_error || selected.error || selected.label || "no validated result";
  });
}

function renderLedger(root, ledgers) {
  const parts = partitionLedgersBySkill(ledgers);
  const keys = Object.keys(parts).sort();
  if (!keys.length) {
    root.innerHTML = `<div class="empty">No ledger partitions yet. Success counts follow digest-bound results per skill_id. There is no global email_confirmed column. Park is an ops overlay.</div>`;
    return;
  }
  root.innerHTML = `<div class="table ledger-table">${keys
    .map((skill) => {
      const part = parts[skill];
      const rows = (part.entries || [])
        .map((e) => {
          const chip =
            e.success === true
              ? `<span class="chip ok">success · ${esc(e.label || e.status)}</span>`
              : e.retryable
                ? `<span class="chip warn">retryable · ${esc(e.label || e.status)}</span>`
                : `<span class="chip">${esc(e.label || e.status || "—")}</span>`;
          return `<div class="row ledger-row">
            <div class="row-name">${esc(e.account_id || e.profile || e.job_id)}</div>
            <div class="row-meta">${esc(e.profile || "—")} · ${esc(e.geo || "—")}</div>
            <div class="row-meta">${chip}</div>
            <div class="row-meta">${e.disposition === "park" ? "ops park" : esc(e.job_id)}</div>
          </div>`;
        })
        .join("");
      return `<div class="skill-part"><div class="skill-part-h">${esc(skill)} · success ${esc(part.success_count)}</div>${rows}</div>`;
    })
    .join("")}</div>`;
}
