const SHIMMER_KEY = "cloakcli.desktop.shimmer";

const state = {
  route: "chat",
  inspectorOpen: true,
  shimmer: false,
  profiles: [],
  skills: [],
  skillsInvalid: [],
  selectedProfile: null,
  selectedSkill: null,
  status: null,
  shell: null,
  error: null,
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

export function setCatalog({ profiles, skills, skillsInvalid, status, shell, error }) {
  if (profiles) state.profiles = profiles;
  if (skills) state.skills = skills;
  if (skillsInvalid) state.skillsInvalid = skillsInvalid;
  if (status) state.status = status;
  if (shell) state.shell = shell;
  if (error !== undefined) state.error = error;
  if (!state.selectedProfile && state.profiles.length) {
    state.selectedProfile = state.profiles[0].name;
  }
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

export function loadShimmerPref() {
  try {
    state.shimmer = localStorage.getItem(SHIMMER_KEY) === "1";
  } catch {
    state.shimmer = false;
  }
  document.documentElement.classList.toggle("shimmer", state.shimmer);
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
