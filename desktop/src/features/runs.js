import { getState } from "../store.js";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function renderRuns(root) {
  const jobs = getState().status?.jobs_recent || [];
  const running = getState().status?.jobs_running ?? 0;
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">RUNS / HISTORY</div>
        <div class="page-title">Job stubs</div>
      </div>
      <div class="page-sub">${running} running · ${jobs.length} recent (no extract payloads)</div>
    </div>
    <div class="table" id="runs-table"></div>
  `;
  const table = root.querySelector("#runs-table");
  if (!jobs.length) {
    table.innerHTML = `<div class="empty">No job records under data/jobs.</div>`;
    return;
  }
  table.innerHTML = jobs
    .map(
      (j) => `
      <div class="row runs-row">
        <div class="row-name">${esc(j.job_id)}</div>
        <div class="row-meta">${esc(j.skill)}</div>
        <div class="row-meta">${esc(j.profile)}</div>
        <div class="row-meta">${esc(j.state)}</div>
      </div>`,
    )
    .join("");
}
