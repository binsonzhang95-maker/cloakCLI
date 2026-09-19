import {
  fleetConfig,
  fleetStatus,
  fleetSubmit,
  fleetSubmitBatch,
  fleetSync,
} from "../api.js";
import { currentProfile, getState, selectProfile, selectSkill, setCatalog } from "../store.js";
import { redactText } from "../redact.js";

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const form = {
  clientId: "",
  skillId: "",
  digest: "",
  version: "",
  headed: false,
  accountId: "",
  busy: false,
  error: "",
  last: null,
  selectedProfiles: [],
};

function publishedFor(skillId, fleet) {
  const list = fleet?.published || [];
  const hits = list.filter((p) => p.skill_id === skillId && p.published);
  return hits[hits.length - 1] || hits[0] || null;
}

function syncFormFromState() {
  const { fleet, skills, selectedSkill, selectedProfile, profiles } = getState();
  if (!form.skillId) {
    form.skillId = selectedSkill || skills[0]?.name || "";
  }
  const rel = publishedFor(form.skillId, fleet);
  if (rel) {
    form.digest = rel.digest;
    form.version = rel.version;
  }
  const live = (fleet?.clients || []).find((c) => c.online);
  if (!form.clientId) {
    form.clientId = live?.client_id || fleet?.clients?.[0]?.client_id || "";
  }
  if (!form.selectedProfiles.length && selectedProfile) {
    form.selectedProfiles = [selectedProfile];
  }
  if (!form.selectedProfiles.length && profiles[0]) {
    form.selectedProfiles = [profiles[0].name];
  }
}

export async function refreshFleet() {
  try {
    const snap = await fleetStatus();
    setCatalog({ fleet: snap });
  } catch (err) {
    setCatalog({ fleet: { hub_running: false, hub_error: String(err), clients: [], queue: [], published: [], desired: { revision: 0, concurrency: 2, headed: false, interval_ms: 0 } } });
  }
}

export function renderFleet(root) {
  const { fleet, profiles, skills, status } = getState();
  syncFormFromState();
  const clients = fleet?.clients || [];
  const queue = fleet?.queue || [];
  const desired = fleet?.desired || { concurrency: 2, interval_ms: 0, headed: false, revision: 0 };
  const online = clients.filter((c) => c.online);
  const rel = publishedFor(form.skillId, fleet);
  const profile = profiles.find((p) => p.name === form.selectedProfiles[0]) || currentProfile();
  const client = clients.find((c) => c.client_id === form.clientId);
  const synced = !!(
    client &&
    rel &&
    (client.installed || []).some((s) => s.skill_id === form.skillId && s.digest === rel.digest)
  );
  const canSubmit = !!(
    fleet?.hub_running &&
    client?.online &&
    rel &&
    form.selectedProfiles.length &&
    !form.busy
  );
  const blocked = !fleet?.hub_running
    ? "hub offline — will not retarget"
    : !client
      ? "pick a client"
      : !client.online
        ? `client ${client.client_id} offline — will not retarget`
        : !rel
          ? "skill has no published digest"
          : !synced
            ? `client has not ACK'd digest ${shortDigest(rel.digest)} — sync first`
            : profile?.occupied
              ? `profile ${profile.name} occupied (${profile.occupied_by || "in-flight"})`
              : "";

  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-kicker">FLEET</div>
        <div class="page-title">Master dispatch</div>
      </div>
      <div class="page-sub">${esc(online.length)} online · conc ${esc(desired.concurrency)} · interval ${esc(desired.interval_ms)}ms · jobs ${esc(status?.jobs_running ?? 0)}</div>
    </div>
    <div class="fleet-layout">
      <div class="fleet-main">
        <div class="fleet-block">
          <div class="insp-label">CLIENTS</div>
          <div class="table" id="fleet-clients">${renderClients(clients, rel)}</div>
        </div>
        <div class="fleet-block">
          <div class="insp-label">QUEUE</div>
          <div class="table" id="fleet-queue">${renderQueue(queue)}</div>
        </div>
      </div>
      <div class="fleet-side">
        <div class="insp-label">SUBMIT</div>
        <p class="muted fleet-note">Digest-bound. Offline / unsynced / geo mismatch refuses — no silent retarget. Local skill run is debug-only.</p>
        <label>client</label>
        <select id="fleet-client">${clients
          .map(
            (c) =>
              `<option value="${esc(c.client_id)}" ${c.client_id === form.clientId ? "selected" : ""}>${esc(c.client_id)} ${c.online ? "online" : "offline"}</option>`,
          )
          .join("")}</select>
        <label>skill (published digest)</label>
        <select id="fleet-skill">${skills
          .map((s) => {
            const r = publishedFor(s.name, fleet);
            return `<option value="${esc(s.name)}" ${s.name === form.skillId ? "selected" : ""}>${esc(s.name)}${r ? ` @${esc(r.version)}` : " (unpublished)"}</option>`;
          })
          .join("")}</select>
        <div class="kv fleet-kv">
          <span>digest</span><span class="mono">${esc(rel ? rel.digest : "—")}</span>
          <span>version</span><span>${esc(rel ? rel.version : "—")}</span>
          <span>sync</span><span>${synced ? "ACK" : "not ACK'd"}</span>
          <span>geo</span><span>${esc(profile?.geo || profile?.name || "—")}</span>
          <span>anon env</span><span>${profile?.anon_env ? "user-data present" : "no user-data dir"}</span>
          <span>occupied</span><span>${profile?.occupied ? esc(profile.occupied_by || "yes") : "free"}</span>
        </div>
        <label>profiles (batch uses all checked)</label>
        <div class="fleet-profiles" id="fleet-profiles">${profiles
          .map((p) => {
            const on = form.selectedProfiles.includes(p.name);
            return `<label class="check-row compact"><input type="checkbox" data-profile="${esc(p.name)}" ${on ? "checked" : ""}/> ${esc(p.name)} · ${esc(p.geo || "—")} ${p.occupied ? "· occupied" : ""}</label>`;
          })
          .join("") || `<div class="empty">No profiles.</div>`}</div>
        <label>account_id (optional, kept on retry)</label>
        <input id="fleet-account" type="text" spellcheck="false" value="${esc(form.accountId)}" placeholder="stable account id" />
        <label class="check-row compact"><input id="fleet-headed" type="checkbox" ${form.headed ? "checked" : ""}/> headed</label>
        <div class="row-actions">
          <button type="button" id="fleet-sync" ${!fleet?.hub_running || !form.clientId || !form.skillId ? "disabled" : ""}>skill_sync</button>
          <button type="button" id="fleet-submit" ${canSubmit && !blocked ? "" : "disabled"}>submit</button>
          <button type="button" id="fleet-batch" ${canSubmit && form.selectedProfiles.length > 1 && !blocked ? "" : "disabled"}>batch ${form.selectedProfiles.length}</button>
        </div>
        <p class="setup-error" id="fleet-error">${esc(blocked || form.error)}</p>
        ${form.last ? `<pre class="fleet-last">${esc(JSON.stringify(form.last, null, 2))}</pre>` : ""}
        <div class="insp-label">BATCH CONFIG</div>
        <div class="fleet-config">
          <label>concurrency <input id="fleet-conc" type="number" min="1" max="32" value="${esc(desired.concurrency)}" /></label>
          <label>interval_ms <input id="fleet-interval" type="number" min="0" max="600000" value="${esc(desired.interval_ms)}" /></label>
          <button type="button" id="fleet-apply" ${fleet?.hub_running ? "" : "disabled"}>apply on master</button>
        </div>
      </div>
    </div>
  `;

  root.querySelector("#fleet-client")?.addEventListener("change", (e) => {
    form.clientId = e.target.value;
    renderFleet(root);
  });
  root.querySelector("#fleet-skill")?.addEventListener("change", (e) => {
    form.skillId = e.target.value;
    selectSkill(form.skillId);
    const r = publishedFor(form.skillId, getState().fleet);
    form.digest = r?.digest || "";
    form.version = r?.version || "";
    renderFleet(root);
  });
  root.querySelector("#fleet-headed")?.addEventListener("change", (e) => {
    form.headed = e.target.checked;
  });
  root.querySelector("#fleet-account")?.addEventListener("input", (e) => {
    form.accountId = e.target.value.trim();
  });
  root.querySelectorAll("#fleet-profiles input[data-profile]").forEach((box) => {
    box.addEventListener("change", () => {
      form.selectedProfiles = [...root.querySelectorAll("#fleet-profiles input[data-profile]:checked")].map(
        (el) => el.dataset.profile,
      );
      if (form.selectedProfiles[0]) selectProfile(form.selectedProfiles[0]);
      renderFleet(root);
    });
  });
  root.querySelector("#fleet-sync")?.addEventListener("click", () => doSync(root));
  root.querySelector("#fleet-submit")?.addEventListener("click", () => doSubmit(root, false));
  root.querySelector("#fleet-batch")?.addEventListener("click", () => doSubmit(root, true));
  root.querySelector("#fleet-apply")?.addEventListener("click", () => doConfig(root));
}

function shortDigest(d) {
  const s = String(d || "");
  if (s.length <= 16) return s || "—";
  return `${s.slice(0, 8)}…${s.slice(-6)}`;
}

function renderClients(clients, rel) {
  if (!clients.length) return `<div class="empty">No fleet clients in data/fleet.json and hub has none online.</div>`;
  return clients
    .map((c) => {
      const ack = rel
        ? (c.installed || []).some((s) => s.skill_id === rel.skill_id && s.digest === rel.digest)
        : false;
      return `<div class="row fleet-client-row ${c.online ? "is-online" : ""}">
        <div class="row-name">${esc(c.client_id)}</div>
        <div class="row-meta">${c.online ? "online" : "offline"}</div>
        <div class="row-meta">${esc(c.url || "—")}</div>
        <div class="row-meta">${ack ? "digest ACK" : `${(c.installed || []).length} skill(s)`}</div>
      </div>`;
    })
    .join("");
}

function renderQueue(queue) {
  if (!queue.length) return `<div class="empty">No jobs on disk. Submit binds a published digest.</div>`;
  return queue
    .map(
      (j) => `<div class="row fleet-queue-row">
        <div class="row-name">${esc(j.job_id)}</div>
        <div class="row-meta">${esc(j.state)}</div>
        <div class="row-meta">${esc(j.client_id)} · ${esc(j.profile)}</div>
        <div class="row-meta">${esc(j.skill)} ${j.digest ? shortDigest(j.digest) : ""}</div>
      </div>`,
    )
    .join("");
}

async function doSync(root) {
  form.error = "";
  form.busy = true;
  renderFleet(root);
  try {
    const resp = await fleetSync(form.clientId, form.skillId, form.version || null);
    form.last = { cmd: "skill_sync", ok: resp?.ok !== false, digest: resp?.release?.digest || form.digest };
    if (resp?.ok === false) form.error = redactText(resp.error || "skill_sync failed");
    await refreshFleet();
  } catch (err) {
    form.error = redactText(String(err));
  }
  form.busy = false;
  renderFleet(root);
}

function specFor(profile) {
  const st = getState();
  const p = st.profiles.find((x) => x.name === profile);
  return {
    client_id: form.clientId,
    skill_id: form.skillId,
    profile,
    digest: form.digest || null,
    version: form.version || null,
    geo: p?.geo || null,
    account_id: form.accountId || null,
    headed: form.headed,
  };
}

async function doSubmit(root, batch) {
  form.error = "";
  form.busy = true;
  renderFleet(root);
  try {
    const profiles = form.selectedProfiles.length ? form.selectedProfiles : [currentProfile()?.name].filter(Boolean);
    if (!profiles.length) throw new Error("pick a profile");
    if (batch) {
      const rows = await fleetSubmitBatch(profiles.map(specFor));
      form.last = { cmd: "submit_batch", rows };
      const fail = rows.filter((r) => !r.ok);
      if (fail.length) form.error = redactText(fail[0].error || "batch item failed");
    } else {
      const row = await fleetSubmit(specFor(profiles[0]));
      form.last = row;
      if (!row.ok) form.error = redactText(row.error || "submit failed");
    }
    await refreshFleet();
  } catch (err) {
    form.error = redactText(String(err));
  }
  form.busy = false;
  renderFleet(root);
}

async function doConfig(root) {
  form.error = "";
  const conc = Number(root.querySelector("#fleet-conc")?.value || 0);
  const interval = Number(root.querySelector("#fleet-interval")?.value || 0);
  try {
    const resp = await fleetConfig({ concurrency: conc, interval_ms: interval, headed: form.headed });
    form.last = { cmd: "config_update", resp };
    if (resp?.ok === false) form.error = redactText(resp.error || "config failed");
    await refreshFleet();
  } catch (err) {
    form.error = redactText(String(err));
  }
  renderFleet(root);
}

export function clientSyncedDigest(client, skillId, digest) {
  if (!client || !digest) return false;
  return (client.installed || []).some((s) => s.skill_id === skillId && s.digest === digest);
}
