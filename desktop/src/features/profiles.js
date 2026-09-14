import { currentProfile, getState, selectProfile } from "../store.js";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function renderProfiles(root) {
  const { profiles, error } = getState();
  const selected = currentProfile();
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">PROFILES</div>
        <div class="page-title">Local catalog</div>
      </div>
      <div class="page-sub">${profiles.length} profile(s) · proxy redacted · cookie counts only</div>
    </div>
    <div class="table" id="profile-table"></div>
  `;
  const table = root.querySelector("#profile-table");
  if (error && !profiles.length) {
    table.innerHTML = `<div class="empty">${esc(error)}</div>`;
    return;
  }
  if (!profiles.length) {
    table.innerHTML = `<div class="empty">No profiles under CLOAKCLI_HOME/profiles.</div>`;
    return;
  }
  table.innerHTML = profiles
    .map((p) => {
      const sel = selected && selected.name === p.name ? " is-selected" : "";
      const cookies = p.cookie_present
        ? `${p.cookie_count} ck · ${p.cookie_valid}v/${p.cookie_expired}e`
        : "no cookies";
      return `
        <div class="row profiles-row${sel}" data-name="${esc(p.name)}" role="button" tabindex="0">
          <div class="row-name">${esc(p.name)}</div>
          <div class="row-meta">${esc(p.proxy || "direct")} · ${esc(p.notes || "—")}</div>
          <div class="row-meta">${esc(cookies)}</div>
        </div>`;
    })
    .join("");
  table.querySelectorAll(".row").forEach((row) => {
    row.addEventListener("click", () => selectProfile(row.dataset.name));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectProfile(row.dataset.name);
      }
    });
  });
}
