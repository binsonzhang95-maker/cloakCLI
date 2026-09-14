import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export { listen };

export async function shellStatus() {
  return invoke("shell_status");
}

export async function setHome(path) {
  return invoke("set_home", { path });
}

export async function listProfiles() {
  return invoke("list_profiles");
}

export async function listSkills() {
  return invoke("list_skills");
}

export async function opsStatus() {
  return invoke("ops_status");
}

export async function ptyStart(cols, rows) {
  return invoke("pty_start", { cols, rows });
}

export async function ptyWrite(data) {
  return invoke("pty_write", { data });
}

export async function ptyResize(cols, rows) {
  return invoke("pty_resize", { cols, rows });
}

export async function ptyStop() {
  return invoke("pty_stop");
}

export async function teachChatStart(profile, url, spawnBrowser) {
  return invoke("teach_chat_start", {
    profile,
    url: url || null,
    spawnBrowser: spawnBrowser ?? null,
  });
}

export async function teachChatSend(goal, profile, skill) {
  return invoke("teach_chat_send", {
    goal,
    profile: profile || null,
    skill: skill || null,
  });
}

export async function teachChatCancel() {
  return invoke("teach_chat_cancel");
}

export async function teachChatConfirm(yes) {
  return invoke("teach_chat_confirm", { yes });
}

export async function teachChatStatus() {
  return invoke("teach_chat_status");
}

export async function teachChatStop() {
  return invoke("teach_chat_stop");
}

export async function jobStart() {
  return invoke("job_start");
}

export async function jobCancel() {
  return invoke("job_cancel");
}

export async function listRuns() {
  return invoke("list_runs");
}

export async function llmStatus() {
  return invoke("llm_status");
}

export async function teachResumeHint() {
  return invoke("teach_resume_hint");
}
