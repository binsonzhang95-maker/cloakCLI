import { getCurrentWindow } from "@tauri-apps/api/window";
import { listProfiles, listSkills, opsStatus, setHome, shellStatus } from "./api.js";
import {
  currentProfile,
  getState,
  loadShimmerPref,
  selectProfile,
  setCatalog,
  setInspectorOpen,
  setRoute,
  setShimmer,
  subscribe,
  toggleInspector,
} from "./store.js";
import { renderChat } from "./features/teach-chat.js";
import { renderProfiles } from "./features/profiles.js";
import { renderSkills } from "./features/skills.js";
import { renderRuns } from "./features/runs.js";
import { ensureDiagnostics, focusDiagnostics } from "./features/diagnostics.js";

const ROUTES = ["chat", "profiles", "skills", "runs", "diagnostics"];
const appWindow = getCurrentWindow();

function $(id) {
  return document.getElementById(id);
}

function lampClass(state) {
  if (state === "running") return "lamp ok";
  if (state === "stopped") return "lamp";
  if (state === "unknown") return "lamp warn";
  return "lamp err";
}

function shortPath(p) {
  if (!p) return "—";
  const parts = p.split("/").filter(Boolean);
  if (parts.length <= 3) return p;
  return "…/" + parts.slice(-2).join("/");
}

function paintChrome(state) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("is-active", el.dataset.route === state.route);
  });
  document.querySelectorAll(".page").forEach((el) => {
    el.classList.toggle("is-active", el.dataset.route === state.route);
  });
  document.querySelector(".app").classList.toggle("inspector-collapsed", !state.inspectorOpen);
  $("btn-inspector").classList.toggle("is-on", state.inspectorOpen);
  $("session-label").textContent = state.route === "diagnostics" ? "raw tui" : "ops console";

  const st = state.status;
  const profile = currentProfile();
  $("top-env").textContent = `env ${st?.env || "local"}`;
  $("lamp-env").className = "lamp ok";
  if (st?.hub) {
    $("top-hub").textContent = `hub ${st.hub.state}`;
    $("lamp-hub").className = lampClass(st.hub.state);
    $("status-hub").textContent = `hub ${st.hub.state} · ${st.hub.detail}`;
  }
  if (st?.worker) {
    $("top-worker").textContent = `worker ${st.worker.state}`;
    $("lamp-worker").className = lampClass(st.worker.state);
    $("status-worker").textContent = `worker ${st.worker.state}${st.worker.pid ? ` pid ${st.worker.pid}` : ""}`;
  }
  $("top-profile").textContent = `profile ${profile?.name || "—"}`;
  $("top-jobs").textContent = `jobs ${st?.jobs_running ?? 0}`;
  $("status-bin").textContent = st?.binary
    ? `bin ${shortPath(st.binary)}`
    : `bin missing${st?.binary_error ? ` (${st.binary_error})` : ""}`;
  $("status-ver").textContent = `v${st?.version || "—"}`;
  $("nav-counts").textContent = `profiles ${state.profiles.length} · skills ${state.skills.length}`;

  paintInspector(state);
}

function paintInspector(state) {
  const p = currentProfile();
  const el = $("insp-profile");
  if (!p) {
    el.className = "insp-body muted";
    el.textContent = "no profile selected";
  } else {
    el.className = "insp-body";
    el.innerHTML = `<div class="kv">
      <span>name</span><span>${escapeHtml(p.name)}</span>
      <span>proxy</span><span>${escapeHtml(p.proxy || "direct")}</span>
      <span>cookies</span><span>${p.cookie_present ? `${p.cookie_count} (${p.cookie_valid} valid)` : "none"}</span>
      <span>notes</span><span>${escapeHtml(p.notes || "—")}</span>
    </div>`;
  }
  const skills = state.skills.slice(0, 8).map((s) => s.name);
  $("insp-skills").className = "insp-body pre";
  $("insp-skills").textContent = skills.length ? skills.join("\n") : "—";
  const runs = state.status?.jobs_recent || [];
  const runsEl = $("insp-runs");
  if (!runs.length) {
    runsEl.className = "insp-body muted";
    runsEl.textContent = "—";
  } else {
    runsEl.className = "insp-body";
    runsEl.innerHTML = runs
      .slice(0, 6)
      .map(
        (j) =>
          `<div class="insp-run"><span>${escapeHtml(j.job_id)}</span><span>${escapeHtml(j.state)}</span></div>`,
      )
      .join("");
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function paintRoute(state) {
  if (state.route === "chat") renderChat($("page-chat"));
  if (state.route === "profiles") renderProfiles($("page-profiles"));
  if (state.route === "skills") renderSkills($("page-skills"));
  if (state.route === "runs") renderRuns($("page-runs"));
  if (state.route === "diagnostics") {
    ensureDiagnostics().then(() => focusDiagnostics());
  }
}

let lastRoute = null;
let lastProfile = null;
let lastCatalogSig = "";

function onState(state) {
  paintChrome(state);
  const sig = JSON.stringify({
    n: state.profiles.length,
    s: state.skills.length,
    e: state.error,
    j: state.status?.jobs_recent?.length,
  });
  if (state.route !== lastRoute || state.selectedProfile !== lastProfile || sig !== lastCatalogSig) {
    paintRoute(state);
    lastRoute = state.route;
    lastProfile = state.selectedProfile;
    lastCatalogSig = sig;
  }
}

async function syncMaximizeIcon() {
  const maximized = await appWindow.isMaximized();
  $("icon-maximize").classList.toggle("hidden", maximized);
  $("icon-restore").classList.toggle("hidden", !maximized);
  $("titlebar-maximize").title = maximized ? "Restore" : "Maximize";
}

function typingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

export async function bootShell() {
  loadShimmerPref();
  subscribe(onState);
  onState(getState());

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => setRoute(btn.dataset.route));
  });
  $("btn-inspector").addEventListener("click", () => toggleInspector());
  $("btn-inspector-close").addEventListener("click", () => setInspectorOpen(false));
  $("btn-settings").addEventListener("click", () => openSettings());
  $("settings-close").addEventListener("click", () => $("settings").classList.add("hidden"));
  $("settings-shimmer").addEventListener("change", (e) => setShimmer(e.target.checked));
  $("settings-save").addEventListener("click", async () => {
    $("settings-error").textContent = "";
    try {
      await setHome($("settings-home").value);
      $("settings").classList.add("hidden");
      await refreshCatalog();
    } catch (err) {
      $("settings-error").textContent = String(err);
    }
  });

  $("titlebar-minimize").addEventListener("click", () => appWindow.minimize());
  $("titlebar-maximize").addEventListener("click", async () => {
    await appWindow.toggleMaximize();
    await syncMaximizeIcon();
  });
  $("titlebar-close").addEventListener("click", () => appWindow.close());
  document.querySelector(".titlebar-drag").addEventListener("mousedown", (event) => {
    if (event.buttons !== 1) return;
    if (event.target.closest("button")) return;
    if (event.detail === 2) {
      appWindow.toggleMaximize().then(syncMaximizeIcon);
    }
  });

  $("home-save").addEventListener("click", async () => {
    $("setup-error").textContent = "";
    try {
      await setHome($("home-input").value);
      $("setup").classList.add("hidden");
      await refreshCatalog();
    } catch (err) {
      $("setup-error").textContent = String(err);
    }
  });
  $("home-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("home-save").click();
  });

  window.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const route = getState().route;
    if (route === "diagnostics" && typingTarget(event.target) === false) {
      // xterm captures keys; don't steal digits while the TUI is focused
      const diagActive = $("page-diagnostics").classList.contains("is-active");
      const inTerm = event.target.closest?.(".xterm");
      if (diagActive && (inTerm || document.activeElement?.closest?.(".xterm"))) {
        return;
      }
    }
    if (typingTarget(event.target)) return;
    if (event.key >= "1" && event.key <= "5") {
      event.preventDefault();
      setRoute(ROUTES[Number(event.key) - 1]);
    } else if (event.key === "[") {
      event.preventDefault();
      toggleInspector();
    } else if (event.key === ",") {
      event.preventDefault();
      openSettings();
    } else if (event.key === "Escape") {
      $("settings").classList.add("hidden");
    }
  });

  window.addEventListener("resize", () => syncMaximizeIcon());
  await syncMaximizeIcon();
  await refreshCatalog();
}

function openSettings() {
  const st = getState().status;
  $("settings-home").value = st?.home || "";
  $("settings-shimmer").checked = getState().shimmer;
  $("settings-error").textContent = "";
  $("settings").classList.remove("hidden");
  $("settings-home").focus();
}

export async function refreshCatalog() {
  try {
    const [shell, status] = await Promise.all([shellStatus(), opsStatus()]);
    setCatalog({ shell, status, error: null });
    if (!status.home) {
      $("setup").classList.remove("hidden");
      $("setup-error").textContent = status.home_error || "";
      if (status.home) $("home-input").value = status.home;
      return;
    }
    $("setup").classList.add("hidden");
    const [profiles, skillList] = await Promise.all([listProfiles(), listSkills()]);
    setCatalog({
      profiles,
      skills: skillList.skills || [],
      skillsInvalid: skillList.invalid || [],
    });
    const selected = getState().selectedProfile;
    if (selected && !profiles.some((p) => p.name === selected) && profiles[0]) {
      selectProfile(profiles[0].name);
    }
  } catch (err) {
    setCatalog({ error: String(err) });
  }
}
