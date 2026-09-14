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
