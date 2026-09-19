const SHIMMER_KEY = "cloakcli.desktop.shimmer";

const state = {
  route: "fleet",
  inspectorOpen: true,
  shimmer: false,
  profiles: [],
  skills: [],
  skillsInvalid: [],
  selectedProfile: null,
  selectedSkill: null,
  selectedRun: null,
  status: null,
  shell: null,
  runs: [],
  llm: null,
  resumeHint: null,
  error: null,
  fleet: null,
  ledgers: [],
  runsTab: "history",
};

const listeners = new Set();

export function getState() {
  return state;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn(state);
}

export function setRoute(route) {
  state.route = route;
  emit();
}

export function setInspectorOpen(open) {
  state.inspectorOpen = open;
  emit();
}

export function toggleInspector() {
  state.inspectorOpen = !state.inspectorOpen;
  emit();
}

export function setCatalog({
  profiles,
  skills,
  skillsInvalid,
  status,
  shell,
  runs,
  llm,
  resumeHint,
  fleet,
  ledgers,
  error,
}) {
  if (profiles) state.profiles = profiles;
  if (skills) state.skills = skills;
  if (skillsInvalid) state.skillsInvalid = skillsInvalid;
  if (status) state.status = status;
  if (shell) state.shell = shell;
  if (runs) state.runs = runs;
  if (llm !== undefined) state.llm = llm;
  if (resumeHint !== undefined) state.resumeHint = resumeHint;
  if (fleet !== undefined) state.fleet = fleet;
  if (ledgers) state.ledgers = ledgers;
  if (error !== undefined) state.error = error;
  if (!state.selectedProfile && state.profiles.length) {
    state.selectedProfile = state.profiles[0].name;
  }
  emit();
}

export function selectRun(id) {
  state.selectedRun = id || null;
  emit();
}

export function upsertRun(run) {
  if (!run || !run.id) return;
  const next = Array.isArray(state.runs) ? state.runs.slice() : [];
  const i = next.findIndex((r) => r.id === run.id);
  if (i >= 0) next[i] = { ...next[i], ...run };
  else next.unshift(run);
  next.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  state.runs = next.slice(0, 50);
  emit();
}

export function selectProfile(name) {
  state.selectedProfile = name;
  emit();
}

export function selectSkill(name) {
  state.selectedSkill = name;
  emit();
}

export function setRunsTab(tab) {
  state.runsTab = tab === "ledger" ? "ledger" : "history";
  emit();
}

export function loadShimmerPref() {
  try {
    state.shimmer = localStorage.getItem(SHIMMER_KEY) === "1";
  } catch {
    state.shimmer = false;
  }
  if (typeof document !== "undefined") {
    document.documentElement.classList.toggle("shimmer", state.shimmer);
  }
}

export function setShimmer(on) {
  state.shimmer = Boolean(on);
  try {
    localStorage.setItem(SHIMMER_KEY, state.shimmer ? "1" : "0");
  } catch {
    // ignore
  }
  document.documentElement.classList.toggle("shimmer", state.shimmer);
  emit();
}

export function currentProfile() {
  return state.profiles.find((p) => p.name === state.selectedProfile) || null;
}
