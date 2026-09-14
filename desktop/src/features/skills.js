import { getState } from "../store.js";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function renderSkills(root) {
  const { skills, skillsInvalid } = getState();
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">SKILLS</div>
        <div class="page-title">Catalog</div>
      </div>
      <div class="page-sub">${skills.length} skill(s) · steps not sent to UI</div>
    </div>
    <div class="table" id="skill-table"></div>
  `;
  const table = root.querySelector("#skill-table");
  if (!skills.length && !skillsInvalid.length) {
    table.innerHTML = `<div class="empty">No skills under CLOAKCLI_HOME/skills.</div>`;
    return;
  }
  const rows = skills
    .map(
      (s) => `
      <div class="row skills-row">
        <div class="row-name">${esc(s.name)}</div>
        <div class="row-meta">${esc(s.description || "—")}</div>
        <div class="row-meta">${s.step_count} steps</div>
        <div class="row-meta">${esc(s.on_stall || "fail")}</div>
      </div>`,
    )
    .join("");
  const invalid = skillsInvalid
    .map(
      (s) => `
      <div class="row skills-row">
        <div class="row-name">invalid</div>
        <div class="row-meta">${esc(s.path)}</div>
        <div class="row-meta">${esc(s.error)}</div>
        <div class="row-meta"></div>
      </div>`,
    )
    .join("");
  table.innerHTML = rows + invalid;
}
